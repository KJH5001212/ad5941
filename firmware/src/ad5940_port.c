/*
 * ============================================================
 *  ad5940_port.c
 *  ADI AD5940 라이브러리 <-> nRF52832 / Zephyr HAL 포트 계층
 *  ------------------------------------------------------------
 *  ad5940.c 가 요구하는 저수준 함수만 구현한다:
 *    - AD5940_ReadWriteNBytes : SPI 풀듀플렉스 전송 (CS 를 건드리지 않음!)
 *    - AD5940_CsClr / CsSet    : CS 물리 LOW / HIGH
 *    - AD5940_RstClr / RstSet  : RESET 물리 LOW / HIGH
 *    - AD5940_Delay10us        : 10us 단위 지연
 *    - AD5940_MCUResourceInit  : SPI/GPIO 초기화
 *    - AD5940_GetMCUIntFlag / ClrMCUIntFlag : GP0 인터럽트 플래그
 *
 *  라이브러리(ad5940.c)가 AD5940_ReadReg/WriteReg 안에서 CsClr...CsSet 로
 *  프레임을 직접 감싸므로, 여기 SPI 전송은 절대 CS 를 토글하면 안 된다.
 *  => spi_config 에 cs 를 넣지 않고, CS 를 별도 GPIO 로 수동 제어한다.
 * ============================================================
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/logging/log.h>

#include "ad5940.h"

LOG_MODULE_REGISTER(ad5940_port, LOG_LEVEL_INF);

/* ---- SPI ---- */
#define SPI_NODE DT_NODELABEL(spi2)
static const struct device *const spi_dev = DEVICE_DT_GET(SPI_NODE);

/* CS 는 드라이버가 관리하지 않는다 (cs 필드 비움). SPI mode 0, 8bit, MSB first. */
static const struct spi_config spi_cfg = {
	.frequency = 1000000U,                      /* 1MHz: 신호 무결성 우선(브링업). 확인되면 올려도 됨(상한 8MHz) */
	.operation = SPI_WORD_SET(8) | SPI_TRANSFER_MSB |
		     SPI_OP_MODE_MASTER,            /* CPOL=0 CPHA=0 = mode 0 */
	.slave = 0,
	.cs = { 0 },
};

/* ---- 제어 GPIO (app.overlay 의 zephyr,user) ---- */
#define ZUSER DT_PATH(zephyr_user)
static const struct gpio_dt_spec cs_gpio  = GPIO_DT_SPEC_GET(ZUSER, cs_gpios);
static const struct gpio_dt_spec rst_gpio = GPIO_DT_SPEC_GET(ZUSER, reset_gpios);
static const struct gpio_dt_spec int_gpio = GPIO_DT_SPEC_GET(ZUSER, int_gpios);

/* ---- INT 플래그 ---- */
static volatile uint32_t ucInterrupted;
static struct gpio_callback int_cb_data;

static void ad5940_int_isr(const struct device *dev, struct gpio_callback *cb,
			   uint32_t pins)
{
	ARG_UNUSED(dev);
	ARG_UNUSED(cb);
	ARG_UNUSED(pins);
	ucInterrupted = 1;
}

/* ============================================================
 *  SPI 전송 (CS 미제어)
 * ============================================================ */
void AD5940_ReadWriteNBytes(unsigned char *pSendBuffer,
			    unsigned char *pRecvBuffer,
			    unsigned long length)
{
	const struct spi_buf tx_buf = { .buf = pSendBuffer, .len = length };
	const struct spi_buf rx_buf = { .buf = pRecvBuffer, .len = length };
	const struct spi_buf_set tx = { .buffers = &tx_buf, .count = 1 };
	const struct spi_buf_set rx = { .buffers = &rx_buf, .count = 1 };

	int err = spi_transceive(spi_dev, &spi_cfg, &tx, &rx);

	if (err) {
		LOG_ERR("spi_transceive failed: %d", err);
	}
}

/* ============================================================
 *  CS / RESET (물리 레벨 직접 구동; overlay 에서 ACTIVE_HIGH 선언)
 * ============================================================ */
void AD5940_CsClr(void)  { gpio_pin_set_dt(&cs_gpio, 0);  }  /* CS LOW  (선택)   */
void AD5940_CsSet(void)  { gpio_pin_set_dt(&cs_gpio, 1);  }  /* CS HIGH (해제)   */
void AD5940_RstClr(void) { gpio_pin_set_dt(&rst_gpio, 0); }  /* RESET LOW (assert) */
void AD5940_RstSet(void) { gpio_pin_set_dt(&rst_gpio, 1); }  /* RESET HIGH(release)*/

/* ============================================================
 *  지연 (인자는 10us 단위)
 * ============================================================ */
void AD5940_Delay10us(uint32_t time)
{
	if (time == 0U) {
		return;
	}
	/* 10us * time. 긴 지연은 CPU 를 재우고, 짧은 지연은 busy-wait. */
	if (time >= 100U) {                 /* >=1ms 는 sleep */
		k_usleep(time * 10U);
	} else {
		k_busy_wait(time * 10U);
	}
}

/* ============================================================
 *  MCU 인터럽트 플래그 (라이브러리가 FIFO/시퀀서 인터럽트에 사용)
 * ============================================================ */
uint32_t AD5940_GetMCUIntFlag(void) { return ucInterrupted; }
uint32_t AD5940_ClrMCUIntFlag(void) { ucInterrupted = 0; return 1; }

/* ============================================================
 *  MCU 리소스 초기화 (SPI 준비 확인 + GPIO 설정)
 *  반환 0 = 성공.
 * ============================================================ */
uint32_t AD5940_MCUResourceInit(void *pCfg)
{
	ARG_UNUSED(pCfg);

	if (!device_is_ready(spi_dev)) {
		LOG_ERR("SPI device not ready");
		return 1;
	}
	if (!gpio_is_ready_dt(&cs_gpio) || !gpio_is_ready_dt(&rst_gpio) ||
	    !gpio_is_ready_dt(&int_gpio)) {
		LOG_ERR("control GPIO not ready");
		return 1;
	}

	/* CS 해제(HIGH), RESET 릴리스(HIGH) 상태로 시작 */
	gpio_pin_configure_dt(&cs_gpio, GPIO_OUTPUT_INACTIVE);
	gpio_pin_set_dt(&cs_gpio, 1);
	gpio_pin_configure_dt(&rst_gpio, GPIO_OUTPUT_INACTIVE);
	gpio_pin_set_dt(&rst_gpio, 1);

	/* INT: active-low 엣지 인터럽트 */
	gpio_pin_configure_dt(&int_gpio, GPIO_INPUT);
	gpio_pin_interrupt_configure_dt(&int_gpio, GPIO_INT_EDGE_TO_ACTIVE);
	gpio_init_callback(&int_cb_data, ad5940_int_isr, BIT(int_gpio.pin));
	gpio_add_callback(int_gpio.port, &int_cb_data);

	ucInterrupted = 0;
	return 0;
}
