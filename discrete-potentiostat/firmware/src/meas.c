/*
 * meas.c — SAADC 측정 엔진 구현 (nRF52832)
 * ------------------------------------------------------------
 *  - 차동 12bit: AIN0(TIA_OUT) − AIN1(VREF 1.024V), 내부 0.6V 레퍼런스
 *  - 게인 테이블 {4, 1, 1/2} → FS ±0.15/±0.6/±1.2 V (index0 = 최고감도)
 *  - HW 오버샘플링(≤256, 단일채널 burst) — RF 가 작을수록 크게 설정
 *  - acquisition 40 µs: 소스 임피던스(RC 1 kΩ + TIA 출력) 대응
 *  - 게인 전환 시 SAADC 오프셋 캘리브레이션 재수행
 */
#include "meas.h"
#include "pstat.h"

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/logging/log.h>
#include <hal/nrf_saadc.h>

LOG_MODULE_REGISTER(meas, LOG_LEVEL_INF);

#define ADC_NODE  DT_NODELABEL(adc)
static const struct device *adc_dev = DEVICE_DT_GET(ADC_NODE);

static const struct gpio_dt_spec afe_pwr =
	GPIO_DT_SPEC_GET(DT_PATH(zephyr_user), afe_pwr_gpios);
static const struct gpio_dt_spec led =
	GPIO_DT_SPEC_GET(DT_PATH(zephyr_user), led_gpios);
static const struct gpio_dt_spec chg =
	GPIO_DT_SPEC_GET(DT_PATH(zephyr_user), chg_gpios);

/* 게인 테이블: index0 = 최고감도 (FS 좁음) */
static const struct {
	enum adc_gain gain;
	float fs_v;                 /* |ΔV| 풀스케일 = 0.6 V / gain */
} GAIN_TAB[GAIN_N] = {
	{ ADC_GAIN_4,   0.15f },    /* |I| < 150 nA @1 MΩ, LSB 73 pA */
	{ ADC_GAIN_1,   0.6f  },
	{ ADC_GAIN_1_2, 1.2f  },    /* −0.9 / +2.1 µA 풀레인지 @1 MΩ */
};

#define CH_MEAS  0
#define CH_VBAT  1
#define CH_QI    2

static int  cur_gain = GAIN_N - 1;
static bool need_calib = true;      /* 게인 변경/부팅 후 1회 SAADC 캘리브레이션 */

static int setup_meas_channel(int gain_idx)
{
	const struct adc_channel_cfg cfg = {
		.gain             = GAIN_TAB[gain_idx].gain,
		.reference        = ADC_REF_INTERNAL,          /* 0.6 V */
		.acquisition_time = ADC_ACQ_TIME(ADC_ACQ_TIME_MICROSECONDS, 40),
		.channel_id       = CH_MEAS,
		.differential     = 1,
		.input_positive   = NRF_SAADC_AIN0,            /* TIA_OUT (P0.02) */
		.input_negative   = NRF_SAADC_AIN1,            /* VREF    (P0.03) */
	};
	return adc_channel_setup(adc_dev, &cfg);
}

static int setup_vbat_channel(void)
{
	const struct adc_channel_cfg cfg = {
		.gain             = ADC_GAIN_1_4,              /* FS 2.4 V (VBAT/2 ≤ 2.1) */
		.reference        = ADC_REF_INTERNAL,
		.acquisition_time = ADC_ACQ_TIME(ADC_ACQ_TIME_MICROSECONDS, 40),
		.channel_id       = CH_VBAT,
		.input_positive   = NRF_SAADC_AIN2,            /* VBAT/2 (P0.04) */
	};
	return adc_channel_setup(adc_dev, &cfg);
}

static int setup_qi_channel(void)
{
	const struct adc_channel_cfg cfg = {
		.gain             = ADC_GAIN_1_4,              /* FS 2.4 V (5V/2 → 2.5V 는 클램프돼도 판정 무관) */
		.reference        = ADC_REF_INTERNAL,
		.acquisition_time = ADC_ACQ_TIME(ADC_ACQ_TIME_MICROSECONDS, 40),
		.channel_id       = CH_QI,
		.input_positive   = NRF_SAADC_AIN3,            /* Charge = MCP73832_VDD/2 (P0.05) */
	};
	return adc_channel_setup(adc_dev, &cfg);
}

int meas_init(void)
{
	if (!device_is_ready(adc_dev)) {
		LOG_ERR("ADC not ready");
		return -ENODEV;
	}
	int err = gpio_pin_configure_dt(&afe_pwr, GPIO_OUTPUT_INACTIVE);
	err |= gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);
	err |= gpio_pin_configure_dt(&chg, GPIO_INPUT);
	if (err) {
		LOG_ERR("gpio config failed");
		return -EIO;
	}
	err = setup_meas_channel(cur_gain);
	err |= setup_vbat_channel();
	err |= setup_qi_channel();
	return err ? -EIO : 0;
}

void afe_power(bool on)
{
	gpio_pin_set_dt(&afe_pwr, on ? 1 : 0);
}

int meas_set_gain(int gain_idx)
{
	if (gain_idx < 0 || gain_idx >= GAIN_N) {
		return -EINVAL;
	}
	if (gain_idx == cur_gain) {
		return 0;
	}
	int err = setup_meas_channel(gain_idx);
	if (err == 0) {
		cur_gain = gain_idx;
		need_calib = true;   /* 게인 바뀌면 SAADC 오프셋 재캘리브레이션 */
	}
	return err;
}

float meas_fs_volts(void)
{
	return GAIN_TAB[cur_gain].fs_v;
}

/* oversample(1..256) → log2 절사 */
static uint8_t os_log2(uint16_t os)
{
	uint8_t n = 0;
	while ((1U << (n + 1)) <= os && n < 8) {
		n++;
	}
	return n;
}

int meas_read_dv(uint16_t oversample, float *dv)
{
	int16_t raw;
	struct adc_sequence seq = {
		.channels     = BIT(CH_MEAS),
		.buffer       = &raw,
		.buffer_size  = sizeof(raw),
		.resolution   = 12,
		.oversampling = os_log2(oversample),
		.calibrate    = need_calib,
	};
	int err = adc_read(adc_dev, &seq);
	if (err) {
		return err;
	}
	need_calib = false;
	/* 차동 12bit: −2048..2047 → ΔV = raw/2048 × FS */
	*dv = ((float)raw / 2048.0f) * GAIN_TAB[cur_gain].fs_v;
	return 0;
}

int meas_vbat_mv(void)
{
	int16_t raw;
	struct adc_sequence seq = {
		.channels    = BIT(CH_VBAT),
		.buffer      = &raw,
		.buffer_size = sizeof(raw),
		.resolution  = 12,
	};
	int err = adc_read(adc_dev, &seq);
	if (err) {
		return err;
	}
	if (raw < 0) {
		raw = 0;
	}
	/* 단극 12bit: V = raw/4096 × 2.4 V, 분압 1:1 → ×2 → mV */
	return (int)((raw * 2400L * 2L) / 4096L);
}

int meas_qi_mv(void)
{
	int16_t raw;
	struct adc_sequence seq = {
		.channels    = BIT(CH_QI),
		.buffer      = &raw,
		.buffer_size = sizeof(raw),
		.resolution  = 12,
	};
	int err = adc_read(adc_dev, &seq);
	if (err) {
		return err;
	}
	if (raw < 0) {
		raw = 0;
	}
	/* 단극 12bit: V = raw/4096 × 2.4 V, 분압 1:1 → ×2 → mV (패드 위 ≈5000) */
	return (int)((raw * 2400L * 2L) / 4096L);
}

bool meas_qi_present(void)
{
	/* BQ51013B OUT 은 패드 위에서 5V 정전압 — 절반인 2.5V 를 한참 밑도는
	 * 2V 를 문턱으로 (패드 밖은 방전된 채 ~0V) */
	return meas_qi_mv() > 2000;
}

bool meas_charging(void)
{
	/* /CHG (STAT): 오픈드레인, LOW=충전중. overlay 에서 ACTIVE_LOW 선언
	 * → 논리 1 = 충전중 */
	return gpio_pin_get_dt(&chg) == 1;
}

void led_set(bool on)
{
	gpio_pin_set_dt(&led, on ? 1 : 0);
}
