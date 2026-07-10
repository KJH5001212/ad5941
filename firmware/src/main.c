/*
 * ============================================================
 *  AD5941 크로노암페로메트리 포텐쇼스탯 — 앱 제어형
 *  ------------------------------------------------------------
 *  - 전위 +0.5V 고정, LPTIA 전류측정
 *  - 오토레인지 (512k 시작 -> 포화 시 RTIA 하강, 히스테리시스+settle)
 *  - 실행모드: Continuous / Timed(N초) / Cycle(측정 T_on / 대기 T_off, 대기중 바이어스 off)
 *  - BLE NUS: JSON 명령(start/stop/config/ack/status), 무손실 데이터 스트림
 *    · 측정중 연결간격 30ms / 대기·유휴중 1.5s (iOS 호환, supervision 5s)
 *  - 데이터 무결성: 끊겨도 측정 계속, 재연결 시 미ACK분 재전송(databuf)
 *
 *  구조: meas_thread = AFE 상태머신+측정 -> databuf 적재
 *        main loop   = databuf -> BLE 전송(무손실) + 상태 보고
 *        nus_received= JSON 명령 파싱 -> 상태머신/ACK 신호
 * ============================================================
 */

#include <zephyr/kernel.h>
#include <zephyr/sys/util.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/services/nus.h>

#include "ad5940.h"
#include "pstat.h"
#include "databuf.h"
#include "cmd.h"

LOG_MODULE_REGISTER(pstat, LOG_LEVEL_INF);

/* ===================== 고정 상수 ===================== */
#define CELL_BIAS_MV   500.0f   /* 전위 고정: V(WE)-V(RE) = +0.5V */
#define VZERO_MV       1100.0f  /* WE 동작점 */
#define ADC_PGA        ADCPGA_1P5
#define ADC_VREF       1.82f
#define SINC2_SPS      150      /* ADCSINC2OSR_1333 기준 대략 출력율 */
#define DAC6_LSB_MV    (2200.0f / 63.0f)
#define DAC12_LSB_MV   (2200.0f / 4095.0f)

/* 오토레인지 임계 (|Vadc|) */
#define SAT_HI_V       0.60f    /* 초과 시 레인지 낮춤(범위↑) */
#define SAT_LO_V       0.05f    /* 미만 시 레인지 높임(감도↑) */
#define AR_SETTLE      4        /* 레인지 변경 후 버릴 출력샘플 수 */

/* BLE 연결간격 (1.25ms 단위), supervision timeout (10ms 단위) */
#define CONN_FAST_MIN  24       /* 30ms */
#define CONN_FAST_MAX  40       /* 50ms */
#define CONN_REST_MIN  1120     /* 1400ms */
#define CONN_REST_MAX  1200     /* 1500ms */
#define CONN_TIMEOUT   500      /* 5000ms (iOS: interval*3 < timeout) */

/* RTIA 오토레인지 테이블: index0=최고감도(512k, ~1nA) ... 낮을수록 큰 전류.
 * ohm 값은 ADI 라이브러리 LpRtiaTable(Rload=100R 보정) 기준 유효값.
 * 1K~512K 는 공칭=유효 동일, 200R 만 Rload 영향으로 유효 ~110R. */
static const struct { uint8_t sel; float ohm; } RTIA_TAB[] = {
	{ LPTIARTIA_512K, 512000.0f }, { LPTIARTIA_256K, 256000.0f },
	{ LPTIARTIA_128K, 128000.0f }, { LPTIARTIA_64K,  64000.0f },
	{ LPTIARTIA_32K,   32000.0f }, { LPTIARTIA_16K,  16000.0f },
	{ LPTIARTIA_8K,     8000.0f }, { LPTIARTIA_4K,    4000.0f },
	{ LPTIARTIA_2K,     2000.0f }, { LPTIARTIA_1K,    1000.0f },
	{ LPTIARTIA_200R,    110.0f },
};
#define RTIA_N ((int)ARRAY_SIZE(RTIA_TAB))

/* ===================== 진단 전역 (SWD로 직접 읽기) ===================== */
volatile uint32_t g_adiid[4];
volatile uint32_t g_chipid;

/* ===================== 상태 ===================== */
static atomic_t g_state = ATOMIC_INIT(PSTAT_IDLE);
static volatile bool stop_req;
static K_SEM_DEFINE(start_sem, 0, 1);

static struct pstat_config g_cfg = {  /* 기본값 */
	.rate_hz = 10, .mode = PSTAT_MODE_CONTINUOUS, .autorange = true,
	.range_idx = 0, .duration_s = 60, .on_s = 5, .off_s = 295, .cycles = 0,
};
static K_MUTEX_DEFINE(cfg_lock);

static struct pstat_config rc;   /* run copy (run() 진입 시 스냅샷) */
static int      cur_range;       /* 현재 RTIA_TAB 인덱스 */
static int      ar_settle;       /* 오토레인지 '결정' 억제 카운터 (진동 방지) */
static int      range_flush;     /* 레인지 변경 후 버릴 SINC2 개수 (세틀 데이터 폐기) */
static uint32_t run_t0;          /* 런 시작 uptime ms */
static uint32_t run_cycle;       /* 현재 사이클 번호 */
static uint32_t next_due;        /* 시간기반 페이싱: 다음 출력샘플 마감(uptime ms) */
static volatile bool status_req;

/* ===================== BLE 전방선언 ===================== */
static struct bt_conn *current_conn;
static K_MUTEX_DEFINE(conn_lock);
static volatile bool notif_enabled;
static void ble_set_fast(bool fast);

/* current_conn 을 락 안에서 ref 잡아 반환 (disconnect 레이스 방지).
 * 사용 후 반드시 bt_conn_unref(). NULL 이면 미연결. */
static struct bt_conn *conn_get(void)
{
	struct bt_conn *c = NULL;
	k_mutex_lock(&conn_lock, K_FOREVER);
	if (current_conn) {
		c = bt_conn_ref(current_conn);
	}
	k_mutex_unlock(&conn_lock);
	return c;
}

/* ===================== AD5941 구성 ===================== */
static int ad5941_afe_init(void)
{
	AD5940_HWReset();
	AD5940_Initialize();
	for (int i = 0; i < 4; i++) {
		g_adiid[i] = AD5940_ReadReg(REG_AFECON_ADIID);
	}
	g_chipid = AD5940_ReadReg(REG_AFECON_CHIPID);
	LOG_INF("ADIID=0x%04x %04x %04x %04x CHIPID=0x%04x (expect 0x%04x)",
		g_adiid[0], g_adiid[1], g_adiid[2], g_adiid[3], g_chipid, AD5940_ADIID);
	return (g_adiid[0] == AD5940_ADIID) ? 0 : -1;
}

/* LP loop: RTIA 선택 + 바이어스 on/off 파라미터화 */
static void afe_lploop_cfg(uint8_t rtia_sel, bool bias_on)
{
	LPLoopCfg_Type lp;
	float vbias_mv = VZERO_MV - CELL_BIAS_MV;

	AD5940_StructInit(&lp, sizeof(lp));
	lp.LpDacCfg.LpdacSel      = LPDAC0;
	lp.LpDacCfg.LpDacSrc      = LPDACSRC_MMR;
	lp.LpDacCfg.LpDacVzeroMux = LPDACVZERO_6BIT;
	lp.LpDacCfg.LpDacVbiasMux = LPDACVBIAS_12BIT;
	lp.LpDacCfg.LpDacRef      = LPDACREF_2P5;
	lp.LpDacCfg.DataRst       = bFALSE;
	lp.LpDacCfg.PowerEn       = bias_on ? bTRUE : bFALSE;
	lp.LpDacCfg.LpDacSW        = LPDACSW_VBIAS2LPPA | LPDACSW_VZERO2LPTIA;
	lp.LpDacCfg.DacData6Bit    = (uint16_t)((VZERO_MV - 200.0f) / DAC6_LSB_MV + 0.5f);
	lp.LpDacCfg.DacData12Bit   = (uint16_t)((vbias_mv - 200.0f) / DAC12_LSB_MV + 0.5f);

	lp.LpAmpCfg.LpAmpSel   = LPAMP0;
	lp.LpAmpCfg.LpAmpPwrMod = LPAMPPWR_NORM;
	lp.LpAmpCfg.LpPaPwrEn  = bias_on ? bTRUE : bFALSE;
	lp.LpAmpCfg.LpTiaPwrEn = bias_on ? bTRUE : bFALSE;
	lp.LpAmpCfg.LpTiaRf    = LPTIARF_1M;
	lp.LpAmpCfg.LpTiaRload = LPTIARLOAD_100R;
	lp.LpAmpCfg.LpTiaRtia  = rtia_sel;
	lp.LpAmpCfg.LpTiaSW    = LPTIASW(2) | LPTIASW(4) | LPTIASW(5);

	AD5940_LPLoopCfgS(&lp);
}

static void afe_adc_cfg(void)
{
	ADCBaseCfg_Type adc;
	ADCFilterCfg_Type filt;

	adc.ADCMuxP = ADCMUXP_LPTIA0_P;
	adc.ADCMuxN = ADCMUXN_LPTIA0_N;
	adc.ADCPga  = ADC_PGA;
	AD5940_ADCBaseCfgS(&adc);

	filt.ADCRate             = ADCRATE_800KHZ;
	filt.ADCSinc3Osr         = ADCSINC3OSR_4;
	filt.ADCSinc2Osr         = ADCSINC2OSR_1333;
	filt.ADCAvgNum           = ADCAVGNUM_16;
	filt.BpSinc3             = bFALSE;
	filt.BpNotch             = bTRUE;
	filt.Sinc2NotchEnable    = bTRUE;
	filt.Sinc3ClkEnable      = bTRUE;
	filt.Sinc2NotchClkEnable = bTRUE;
	filt.DFTClkEnable        = bFALSE;
	filt.WGClkEnable         = bFALSE;
	AD5940_ADCFilterCfgS(&filt);
}

/* SINC2 결과 1개 대기 (stop 시 false). 타임아웃 방어 포함. */
static bool wait_sinc2(void)
{
	int guard = 0;
	while (AD5940_INTCTestFlag(AFEINTC_1, AFEINTSRC_SINC2RDY) == bFALSE) {
		if (stop_req) {
			return false;
		}
		k_msleep(1);
		if (++guard > 1000) {
			return true;   /* 방어적으로 진행 */
		}
	}
	AD5940_INTCClrFlag(AFEINTSRC_SINC2RDY);
	return true;
}

/* AFE 측정 시작 (바이어스 on + ADC 연속변환) */
static void afe_start(int range_idx)
{
	cur_range = range_idx;
	ar_settle = AR_SETTLE;
	afe_lploop_cfg(RTIA_TAB[range_idx].sel, true);
	afe_adc_cfg();
	AD5940_AFECtrlS(AFECTRL_HPREFPWR | AFECTRL_ADCPWR, bTRUE);
	k_msleep(50);
	AD5940_INTCCfg(AFEINTC_1, AFEINTSRC_SINC2RDY, bTRUE);
	AD5940_INTCClrFlag(AFEINTSRC_SINC2RDY);
	AD5940_AFECtrlS(AFECTRL_ADCCNV, bTRUE);
	for (int i = 0; i < 6 && !stop_req; i++) {   /* 파이프라인 안정화 */
		wait_sinc2();
	}
}

/* AFE 정지 + 바이어스 off */
static void afe_stop(void)
{
	AD5940_AFECtrlS(AFECTRL_ADCCNV | AFECTRL_ADCPWR | AFECTRL_HPREFPWR, bFALSE);
	afe_lploop_cfg(RTIA_TAB[cur_range].sel, false);   /* 바이어스 off */
}

/* 레인지 전환 (측정 중) */
static void apply_range(int idx)
{
	cur_range = idx;
	afe_lploop_cfg(RTIA_TAB[idx].sel, true);
	ar_settle = AR_SETTLE;   /* 오토레인지 재결정 억제 */
	range_flush = 2;         /* 게인 스텝 세틀 구간 SINC2 폐기 (데이터 오염 방지) */
}

static void autorange_update(float vadc)
{
	if (ar_settle > 0) {
		ar_settle--;
		return;
	}
	float av = (vadc < 0) ? -vadc : vadc;
	if (av > SAT_HI_V && cur_range < RTIA_N - 1) {
		apply_range(cur_range + 1);   /* 감도↓ 범위↑ */
	} else if (av < SAT_LO_V && cur_range > 0) {
		apply_range(cur_range - 1);   /* 감도↑ */
	}
}

/* 한 출력샘플: 시간기반 페이싱 — 다음 마감(next_due)까지 도착한 SINC2 를
 * 전부 평균해 정확히 rate_hz 로 출력 (정수 양자화 오차 없음). stop 시 false. */
static bool sample_once(struct pstat_sample *smp)
{
	uint32_t period_ms = 1000U / rc.rate_hz;   /* rate 1~100 -> 10~1000ms */
	float vsum = 0.0f;
	int cnt = 0;

	/* 레인지 변경 직후 세틀 구간 SINC2 는 폐기 */
	while (range_flush > 0) {
		if (!wait_sinc2()) {
			return false;
		}
		(void)AD5940_ReadAfeResult(AFERESULT_SINC2);
		range_flush--;
	}

	do {
		if (stop_req) {
			return false;
		}
		if (!wait_sinc2()) {
			return false;
		}
		uint32_t code = AD5940_ReadAfeResult(AFERESULT_SINC2);
		vsum += AD5940_ADCCode2Volt(code, ADC_PGA, ADC_VREF);
		cnt++;
	} while ((int32_t)(k_uptime_get_32() - next_due) < 0);

	next_due += period_ms;
	/* 크게 밀렸으면(레인지 flush 등) 마감 재동기화 */
	if ((int32_t)(k_uptime_get_32() - next_due) > (int32_t)period_ms) {
		next_due = k_uptime_get_32() + period_ms;
	}

	float vavg = vsum / (float)cnt;
	smp->current_nA = (vavg / RTIA_TAB[cur_range].ohm) * 1e9f;
	smp->t_ms       = k_uptime_get_32() - run_t0;
	smp->range_idx  = (uint8_t)cur_range;
	if (rc.autorange) {
		autorange_update(vavg);
	}
	return true;
}

/* 측정 구간: dur_ms=0 이면 무한(stop 까지). 샘플을 databuf 에 적재. */
static void measure_phase(uint32_t dur_ms)
{
	uint32_t t0 = k_uptime_get_32();
	struct pstat_sample smp;

	next_due = t0 + 1000U / rc.rate_hz;   /* 페이싱 마감 초기화 */

	while (!stop_req) {
		if (dur_ms && (k_uptime_get_32() - t0) >= dur_ms) {
			break;
		}
		if (!sample_once(&smp)) {
			break;
		}
		databuf_push(&smp);
	}
}

/* 대기 구간(cycle off): stop 시 조기 종료 */
static void rest_delay(uint32_t ms)
{
	uint32_t t0 = k_uptime_get_32();
	while (!stop_req && (k_uptime_get_32() - t0) < ms) {
		k_msleep(50);
	}
}

/* 한 번의 런 실행 (모드에 따라) */
static void run_once(void)
{
	int start_idx = rc.autorange ? 0 : CLAMP(rc.range_idx, 0, RTIA_N - 1);

	databuf_reset();
	run_t0 = k_uptime_get_32();
	run_cycle = 0;
	ble_set_fast(true);
	atomic_set(&g_state, PSTAT_RUN);
	status_req = true;   /* 즉시 상태 통지 (앱이 run 을 바로 인지) */

	if (rc.mode == PSTAT_MODE_CONTINUOUS) {
		afe_start(start_idx);
		measure_phase(0);
		afe_stop();
	} else if (rc.mode == PSTAT_MODE_TIMED) {
		afe_start(start_idx);
		measure_phase(rc.duration_s * 1000U);
		afe_stop();
	} else { /* CYCLE */
		int idx = start_idx;
		while (!stop_req) {
			run_cycle++;
			atomic_set(&g_state, PSTAT_RUN);
			ble_set_fast(true);
			/* 웜업(~90ms: settle+프라이밍)을 on_s 에 포함시켜
			 * 주기 길이/바이어스 시간이 정확히 on_s 가 되게 함 */
			uint32_t t_on0 = k_uptime_get_32();
			afe_start(idx);
			uint32_t warm = k_uptime_get_32() - t_on0;
			uint32_t on_ms = rc.on_s * 1000U;
			measure_phase(on_ms > warm ? on_ms - warm : 1);
			afe_stop();
			if (rc.autorange) {
				idx = cur_range;   /* 수렴한 레인지를 다음 주기로 유지 */
			}
			if (stop_req) {
				break;
			}
			if (rc.cycles && run_cycle >= rc.cycles) {
				break;
			}
			atomic_set(&g_state, PSTAT_CYCLE_REST);   /* 대기: 바이어스 off */
			status_req = true;
			ble_set_fast(false);                      /* 대기중 절전 간격 */
			rest_delay(rc.off_s * 1000U);
		}
	}

	atomic_set(&g_state, PSTAT_IDLE);
	status_req = true;   /* 종료 즉시 통지 */
	ble_set_fast(false);
	LOG_INF("run finished (cycles=%u)", run_cycle);
}

/* ===================== 측정 스레드 ===================== */
static void meas_thread(void)
{
	while (1) {
		k_sem_take(&start_sem, K_FOREVER);
		if (g_adiid[0] != AD5940_ADIID) {
			LOG_ERR("AFE 미확인 — start 거부");
			atomic_set(&g_state, PSTAT_IDLE);   /* CMD_START 의 CAS 롤백 */
			continue;
		}
		k_mutex_lock(&cfg_lock, K_FOREVER);
		rc = g_cfg;   /* 스냅샷 */
		k_mutex_unlock(&cfg_lock);
		if (rc.rate_hz == 0) {
			rc.rate_hz = 1;   /* 0 나눗셈 방어 */
		}
		/* stop_req 는 CMD_START(수락 시점)에서만 클리어 —
		 * 수락~여기 사이 도착한 STOP 이 보존되어 즉시 종료됨 */
		LOG_INF("START mode=%u rate=%uHz auto=%d dur=%us on=%us off=%us cyc=%u",
			rc.mode, rc.rate_hz, (int)rc.autorange,
			rc.duration_s, rc.on_s, rc.off_s, rc.cycles);
		run_once();
	}
}
K_THREAD_DEFINE(meas_tid, 3072, meas_thread, NULL, NULL, NULL, 5, 0, 0);

/* ===================== BLE ===================== */
#define DEVICE_NAME     CONFIG_BT_DEVICE_NAME
#define DEVICE_NAME_LEN (sizeof(DEVICE_NAME) - 1)

static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA(BT_DATA_NAME_COMPLETE, DEVICE_NAME, DEVICE_NAME_LEN),
};
static const struct bt_data sd[] = {
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_NUS_VAL),
};

static void start_advertising(void)
{
	int err = bt_le_adv_start(BT_LE_ADV_CONN_FAST_1, ad, ARRAY_SIZE(ad),
				  sd, ARRAY_SIZE(sd));
	if (err) {
		LOG_ERR("adv start failed (%d)", err);
	} else {
		LOG_INF("Advertising as '%s'", DEVICE_NAME);
	}
}
static void adv_work_handler(struct k_work *work) { start_advertising(); }
static K_WORK_DEFINE(adv_work, adv_work_handler);

/* 연결간격 전환: 측정중=빠름, 대기·유휴=느림(절전). iOS 규칙 충족값. */
static void ble_set_fast(bool fast)
{
	struct bt_conn *c = conn_get();   /* ref 확보 (disconnect 레이스 방지) */
	if (!c) {
		return;
	}
	int err = fast
		? bt_conn_le_param_update(c, BT_LE_CONN_PARAM(CONN_FAST_MIN, CONN_FAST_MAX, 0, CONN_TIMEOUT))
		: bt_conn_le_param_update(c, BT_LE_CONN_PARAM(CONN_REST_MIN, CONN_REST_MAX, 0, CONN_TIMEOUT));
	if (err) {
		LOG_WRN("conn param update (%s) req failed (%d)", fast ? "fast" : "rest", err);
	}
	bt_conn_unref(c);
}

static void mtu_exchange_cb(struct bt_conn *conn, uint8_t err,
			    struct bt_gatt_exchange_params *params)
{
	LOG_INF("MTU exchange %s -> %u", err ? "FAIL" : "OK", bt_gatt_get_mtu(conn));
}
static struct bt_gatt_exchange_params mtu_exchange_params;

static void connected(struct bt_conn *conn, uint8_t err)
{
	if (err) {
		LOG_ERR("Connection failed (err %u)", err);
		k_work_submit(&adv_work);
		return;
	}
	k_mutex_lock(&conn_lock, K_FOREVER);
	current_conn = bt_conn_ref(conn);
	k_mutex_unlock(&conn_lock);
	notif_enabled = false;
	LOG_INF("Connected (MTU=%u)", bt_gatt_get_mtu(conn));

	mtu_exchange_params.func = mtu_exchange_cb;
	bt_gatt_exchange_mtu(conn, &mtu_exchange_params);

	/* 재연결: 미ACK분 처음부터 재전송. 실행중이면 빠른 간격. */
	databuf_rewind_unsent();
	ble_set_fast(atomic_get(&g_state) == PSTAT_RUN);
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	LOG_INF("Disconnected (reason %u) — 측정 계속, 버퍼 누적", reason);
	notif_enabled = false;
	k_mutex_lock(&conn_lock, K_FOREVER);
	if (current_conn) {
		bt_conn_unref(current_conn);
		current_conn = NULL;
	}
	k_mutex_unlock(&conn_lock);
	k_work_submit(&adv_work);
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
};

static void nus_received(struct bt_conn *conn, const uint8_t *data, uint16_t len)
{
	struct cmd c;
	struct pstat_config base;

	k_mutex_lock(&cfg_lock, K_FOREVER);
	base = g_cfg;
	k_mutex_unlock(&cfg_lock);

	if (!cmd_parse((const char *)data, len, &c, &base)) {
		LOG_WRN("cmd parse fail (%u B)", len);
		return;
	}

	switch (c.type) {
	case CMD_ACK:
		databuf_ack(c.ack_seq);
		break;
	case CMD_STOP:
		stop_req = true;
		LOG_INF("cmd: stop");
		break;
	case CMD_CONFIG:
		k_mutex_lock(&cfg_lock, K_FOREVER);
		g_cfg = c.cfg;
		k_mutex_unlock(&cfg_lock);
		LOG_INF("cmd: config");
		break;
	case CMD_START:
		k_mutex_lock(&cfg_lock, K_FOREVER);
		g_cfg = c.cfg;
		k_mutex_unlock(&cfg_lock);
		/* IDLE->RUN 원자 전이로 수락. 이 시점부터 state=RUN 이므로
		 * 이후 도착하는 STOP 의 stop_req 가 절대 유실되지 않음.
		 * (stop_req 클리어는 여기서만 — meas_thread 는 클리어 안함) */
		if (atomic_cas(&g_state, PSTAT_IDLE, PSTAT_RUN)) {
			stop_req = false;
			k_sem_give(&start_sem);
			LOG_INF("cmd: start");
		} else {
			LOG_WRN("cmd: start 무시 (이미 실행중, stop 먼저)");
		}
		break;
	case CMD_STATUS:
		status_req = true;
		break;
	default:
		break;
	}
}

/* NUS notify 활성 콜백 */
static void nus_send_enabled(enum bt_nus_send_status status)
{
	notif_enabled = (status == BT_NUS_SEND_STATUS_ENABLED);
	LOG_INF("NUS notify %s", notif_enabled ? "ENABLED" : "disabled");
}

static struct bt_nus_cb nus_cb = {
	.received = nus_received,
	.send_enabled = nus_send_enabled,
};

static int tx_nus(const uint8_t *data, uint16_t len)
{
	if (!notif_enabled) {
		return -ENOTCONN;
	}
	struct bt_conn *c = conn_get();   /* ref 확보 (disconnect 레이스 방지) */
	if (!c) {
		return -ENOTCONN;
	}
	int err = bt_nus_send(c, data, len);
	bt_conn_unref(c);
	return err;
}

/* ===================== 데이터/상태 전송 ===================== */
#define BATCH_MAX   5     /* 한 프레임 샘플 수 (MTU 여유) */
#define DRAIN_ITERS 8     /* 틱당 최대 배치 수 (재연결 후 캐치업) */

static void tx_status(void)
{
	char line[160];
	const char *st = "idle";
	uint8_t mode;
	uint16_t rate;

	enum pstat_state s = (enum pstat_state)atomic_get(&g_state);
	if (s == PSTAT_RUN) {
		st = "run";
	} else if (s == PSTAT_CYCLE_REST) {
		st = "rest";
	}
	/* rc 는 meas_thread 가 cfg_lock 하에 복사하므로 같은 락으로 스냅샷 (torn read 방지) */
	k_mutex_lock(&cfg_lock, K_FOREVER);
	mode = rc.mode;
	rate = rc.rate_hz;
	k_mutex_unlock(&cfg_lock);

	int n = snprintk(line, sizeof(line),
		"{\"st\":\"%s\",\"mode\":%u,\"rate\":%u,\"cyc\":%u,\"range\":%d,"
		"\"pend\":%u,\"buf\":%u,\"gap\":%d}\n",
		st, mode, rate, run_cycle, cur_range,
		databuf_pending(), databuf_unacked(), (int)databuf_gap());
	tx_nus(line, n);
}

int main(void)
{
	int err;

	LOG_INF("=== AD5941 potentiostat (app-controlled) ===");

	if (AD5940_MCUResourceInit(NULL) != 0) {
		LOG_ERR("MCU resource init failed");
		return 0;
	}
	k_msleep(10);
	if (ad5941_afe_init() != 0) {
		LOG_WRN("ADIID mismatch — BLE 는 올리되 측정 불가");
	}
	/* 부팅 후 AFE 는 꺼둔 상태 (start 명령 대기) */
	afe_lploop_cfg(RTIA_TAB[0].sel, false);

	err = bt_enable(NULL);
	if (err) {
		LOG_ERR("bt_enable failed (%d)", err);
		return 0;
	}
	bt_nus_init(&nus_cb);
	start_advertising();
	LOG_INF("ready. 앱에서 JSON 명령 대기 (start/config/stop/ack/status)");

	uint32_t tick = 0;
	char line[256];
	struct pstat_sample batch[BATCH_MAX];

	while (1) {
		k_msleep(20);
		tick++;

		if (!current_conn || !notif_enabled) {
			if ((tick % 250) == 0) {   /* 5초마다 하트비트 */
				LOG_INF("alive up=%us state=%ld pend=%u buf=%u conn=%d",
					k_uptime_get_32() / 1000U, atomic_get(&g_state),
					databuf_pending(), databuf_unacked(),
					current_conn ? 1 : 0);
			}
			continue;
		}

		/* 데이터 드레인 (무손실: 전송 성공한 배치만 절대위치로 commit) */
		for (int it = 0; it < DRAIN_ITERS; it++) {
			uint32_t base;
			uint32_t k = databuf_peek_batch(batch, BATCH_MAX, &base);
			if (k == 0) {
				break;
			}
			int o = snprintk(line, sizeof(line), "{\"d\":[");
			for (uint32_t i = 0; i < k; i++) {
				size_t rem = (o < (int)sizeof(line)) ? sizeof(line) - o : 0;
				o += snprintk(line + o, rem, "%s[%u,%u,%.3f,%u]",
					i ? "," : "", batch[i].seq, batch[i].t_ms,
					(double)batch[i].current_nA, batch[i].range_idx);
			}
			{
				size_t rem = (o < (int)sizeof(line)) ? sizeof(line) - o : 0;
				o += snprintk(line + o, rem, "]}\n");
			}
			if (o >= (int)sizeof(line)) {
				continue;   /* 트렁케이션 방어(정상 경로선 발생 안함): 이 배치 스킵 */
			}

			if (tx_nus(line, o) == 0) {
				databuf_commit_sent(base, k);
			} else {
				break;   /* 버퍼 참 등: 다음 틱 재시도 (미전송 유지) */
			}
		}

		/* 상태: 1초마다 또는 요청 시 */
		if (status_req || (tick % 50) == 0) {
			status_req = false;
			tx_status();
		}
	}
	return 0;
}
