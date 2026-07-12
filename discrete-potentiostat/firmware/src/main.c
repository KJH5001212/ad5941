/*
 * ============================================================
 *  디스크리트 크로노암페로메트리 포텐쇼스탯 — 앱 제어형
 *  (nRF52832 SAADC 직접측정 — AD5941 없음)
 *  ------------------------------------------------------------
 *  - 전위 +0.512V 하드웨어 고정 (REF35102 1.024V + 1:1 분압)
 *  - LPV802 TIA (RF 교체가능: 1M/5.1M/10M) → SAADC 차동 (AIN0−AIN1)
 *  - SAADC 게인 오토레인지 {4, 1, 1/2} + 히스테리시스 + settle 폐기
 *  - HW 오버샘플링(런타임 설정) + 시간기반 페이싱 소프트 평균
 *  - 실행모드: Continuous / Timed / Cycle (대기중 AFE 전원 off = 분극 방지)
 *  - BLE NUS: JSON 명령(start/stop/config/ack/status/zero/cal),
 *    무손실 스트림 {"d":[[seq,t_ms,nA,range],...]} — AD5941 버전과 포맷 동일
 *  - 측정 파라미터 앱 제어: rate/mode/auto/range + rf/os (NVM 저장)
 *  - 캘리브레이션: zero(게인별 오프셋) / cal(더미셀 스케일) — NVM 저장
 *  - 배터리: VBAT 분압 감시, <3.3V 경고+start 거부, <3.0V System OFF
 *
 *  구조: meas_thread = AFE 전원+SAADC 측정 -> databuf 적재
 *        main loop   = databuf -> BLE 전송(무손실) + 상태/배터리 보고
 *        nus_received= JSON 명령 파싱 -> 상태머신/ACK/캘리브레이션 신호
 * ============================================================
 */

#include <zephyr/kernel.h>
#include <zephyr/sys/util.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/poweroff.h>
#include <zephyr/logging/log.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/services/nus.h>

#include "pstat.h"
#include "meas.h"
#include "store.h"
#include "databuf.h"
#include "cmd.h"

LOG_MODULE_REGISTER(pstat, LOG_LEVEL_INF);

/* ===================== 고정 상수 ===================== */
#define BIAS_MV        512.0f   /* 인가 전위 V(WE)-V(RE) = +0.512V (하드웨어 고정) */
#define AFE_SETTLE_MS  50       /* AFE_PWR on -> REF/앰프/RC 안정화 대기 */

/* 오토레인지 임계 (|ΔV| / FS 비율) — 게인비 4×/2× 에서 진동 없는 값 */
#define SAT_HI_FRAC    0.85f    /* 초과 시 게인 다운 (범위↑) */
#define SAT_LO_FRAC    0.18f    /* 미만 시 게인 업 (감도↑) */
#define AR_SETTLE      4        /* 게인 변경 후 '결정' 억제 샘플 수 */
#define RANGE_FLUSH    1        /* 게인 변경 직후 버릴 변환 수 */

/* 배터리 정책 (LIR2032 무보호 셀 — 펌웨어가 과방전 보호) */
#define VBAT_WARN_MV   3300     /* 경고 + 신규 start 거부 */
#define VBAT_CUT_MV    3000     /* 데이터 플러시 후 System OFF */
#define VBAT_PERIOD_MS 5000

/* BLE 연결간격 (1.25ms 단위), supervision timeout (10ms 단위)
 * — AD5941 버전에서 검증된 상시 빠른 간격 유지 (전환 지연/타임아웃 회피) */
#define CONN_FAST_MIN  24       /* 30ms */
#define CONN_FAST_MAX  40       /* 50ms */
#define CONN_REST_MIN  24
#define CONN_REST_MAX  40
#define CONN_TIMEOUT   500      /* 5000ms (iOS: interval*3 < timeout) */

/* ===================== 상태 ===================== */
static atomic_t g_state = ATOMIC_INIT(PSTAT_IDLE);
static volatile bool stop_req;
static K_SEM_DEFINE(start_sem, 0, 1);

static struct pstat_config g_cfg = {  /* 기본값 (rf/os 는 NVM 이 덮음) */
	.rate_hz = 10, .mode = PSTAT_MODE_CONTINUOUS, .autorange = true,
	.range_idx = 0, .duration_s = 60, .on_s = 5, .off_s = 295, .cycles = 0,
	.rf_ohm = 2000000, .oversample = 64,
};
static struct pstat_cal g_cal = { .scale = 1.0f, .off_nA = { 0 } };
static K_MUTEX_DEFINE(cfg_lock);

static struct pstat_config rc;   /* run copy (run() 진입 시 스냅샷) */
static int      cur_range;       /* 현재 게인 인덱스 (0=최고감도) */
static int      ar_settle;       /* 오토레인지 결정 억제 카운터 */
static int      range_flush;     /* 게인 변경 후 버릴 변환 수 */
static uint32_t run_t0;
static uint32_t run_cycle;
static uint32_t next_due;        /* 시간기반 페이싱: 다음 출력샘플 마감 */
static volatile bool status_req;
static volatile uint8_t cal_req;      /* 0=없음 1=zero 2=scale */
static volatile uint32_t cal_kohm;
static int      g_vbat_mv = -1;       /* 최근 배터리 전압 (main loop 갱신) */
static bool     low_batt;             /* <VBAT_WARN_MV */

/* ===================== BLE 전방선언 ===================== */
static struct bt_conn *current_conn;
static K_MUTEX_DEFINE(conn_lock);
static volatile bool notif_enabled;
static void ble_set_fast(bool fast);
static int tx_nus(const uint8_t *data, uint16_t len);

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

/* ===================== 측정 코어 (SAADC) ===================== */

/* ΔV[V] -> 전류[nA] (RF/캘리브레이션 반영) */
static float dv_to_nA(float dv)
{
	float raw = (dv / (float)rc.rf_ohm) * 1e9f;
	return raw * g_cal.scale - g_cal.off_nA[cur_range];
}

static void apply_range(int idx)
{
	if (meas_set_gain(idx) == 0) {
		cur_range = idx;
		ar_settle = AR_SETTLE;
		range_flush = RANGE_FLUSH;
	}
}

static void autorange_update(float dv)
{
	if (ar_settle > 0) {
		ar_settle--;
		return;
	}
	float av = (dv < 0) ? -dv : dv;
	float fs = meas_fs_volts();
	if (av > SAT_HI_FRAC * fs && cur_range < GAIN_N - 1) {
		apply_range(cur_range + 1);   /* 감도↓ 범위↑ */
	} else if (av < SAT_LO_FRAC * fs && cur_range > 0) {
		apply_range(cur_range - 1);   /* 감도↑ */
	}
}

/* AFE 전원 on + 초기 게인 + 안정화 */
static void afe_start(int range_idx)
{
	afe_power(true);
	k_msleep(AFE_SETTLE_MS);
	cur_range = -1;               /* meas_set_gain 강제 적용 */
	apply_range(range_idx);
	/* 파이프라인/RC 안정화: 첫 변환 몇 개 폐기 */
	float dv;
	for (int i = 0; i < 3 && !stop_req; i++) {
		meas_read_dv(rc.oversample, &dv);
	}
	range_flush = 0;
}

static void afe_stop(void)
{
	afe_power(false);   /* 소모 0 + CE 구동 차단 → 전극 분극 방지 */
}

/* 한 출력샘플: next_due 까지 도착한 HW 오버샘플 변환을 전부 평균해
 * 정확히 rate_hz 로 출력 (AD5941 버전의 SINC2 페이싱과 동일 구조) */
static bool sample_once(struct pstat_sample *smp)
{
	uint32_t period_ms = 1000U / rc.rate_hz;
	float vsum = 0.0f;
	int cnt = 0;
	float dv = 0.0f;

	while (range_flush > 0) {     /* 게인 세틀 구간 폐기 */
		if (stop_req || meas_read_dv(rc.oversample, &dv) != 0) {
			return false;
		}
		range_flush--;
	}

	do {
		if (stop_req) {
			return false;
		}
		if (meas_read_dv(rc.oversample, &dv) != 0) {
			return false;
		}
		vsum += dv;
		cnt++;
	} while ((int32_t)(k_uptime_get_32() - next_due) < 0);

	next_due += period_ms;
	if ((int32_t)(k_uptime_get_32() - next_due) > (int32_t)period_ms) {
		next_due = k_uptime_get_32() + period_ms;   /* 밀림 재동기화 */
	}

	float vavg = vsum / (float)cnt;
	smp->current_nA = dv_to_nA(vavg);
	smp->t_ms       = k_uptime_get_32() - run_t0;
	smp->range_idx  = (uint8_t)cur_range;
	if (rc.autorange) {
		autorange_update(vavg);
	}
	return true;
}

static void measure_phase(uint32_t dur_ms)
{
	uint32_t t0 = k_uptime_get_32();
	struct pstat_sample smp;

	next_due = t0 + 1000U / rc.rate_hz;

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

static void rest_delay(uint32_t ms)
{
	uint32_t t0 = k_uptime_get_32();
	while (!stop_req && (k_uptime_get_32() - t0) < ms) {
		k_msleep(50);
	}
}

static void run_once(void)
{
	int start_idx = rc.autorange ? 0 : CLAMP(rc.range_idx, 0, GAIN_N - 1);

	databuf_reset();
	run_t0 = k_uptime_get_32();
	run_cycle = 0;
	ble_set_fast(true);
	atomic_set(&g_state, PSTAT_RUN);
	status_req = true;
	led_set(true);

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
			led_set(true);
			/* 웜업(settle+프라이밍)을 on_s 에 포함 → 주기 길이 정확 */
			uint32_t t_on0 = k_uptime_get_32();
			afe_start(idx);
			uint32_t warm = k_uptime_get_32() - t_on0;
			uint32_t on_ms = rc.on_s * 1000U;
			measure_phase(on_ms > warm ? on_ms - warm : 1);
			afe_stop();
			if (rc.autorange) {
				idx = cur_range;   /* 수렴 게인을 다음 주기로 유지 */
			}
			if (stop_req) {
				break;
			}
			if (rc.cycles && run_cycle >= rc.cycles) {
				break;
			}
			atomic_set(&g_state, PSTAT_CYCLE_REST);
			status_req = true;
			ble_set_fast(false);
			led_set(false);
			rest_delay(rc.off_s * 1000U);
		}
	}

	atomic_set(&g_state, PSTAT_IDLE);
	status_req = true;
	ble_set_fast(false);
	led_set(false);
	LOG_INF("run finished (cycles=%u)", run_cycle);
}

/* ===================== 캘리브레이션 ===================== */

/* 게인별로 N 회 평균 ΔV[V] (AFE 는 이미 on 상태 가정) */
static float avg_dv(int gain_idx, int n)
{
	apply_range(gain_idx);
	float dv, sum = 0.0f;
	int k = 0;
	for (int i = 0; i < RANGE_FLUSH + 2; i++) {
		meas_read_dv(rc.oversample, &dv);   /* settle 폐기 */
	}
	range_flush = 0;
	for (int i = 0; i < n; i++) {
		if (meas_read_dv(rc.oversample, &dv) == 0) {
			sum += dv;
			k++;
		}
	}
	return k ? (sum / (float)k) : 0.0f;
}

/* zero: 셀 오픈(전극 미연결) 상태에서 게인별 오프셋 저장.
 * cal:  기지저항 R[kΩ] 더미셀 → 기대 I = 512mV/R, scale = 기대/측정 */
static void do_calibration(uint8_t kind, uint32_t kohm)
{
	char line[96];
	int n;

	k_mutex_lock(&cfg_lock, K_FOREVER);
	rc = g_cfg;   /* 최신 rf/os 반영 */
	k_mutex_unlock(&cfg_lock);

	afe_power(true);
	k_msleep(AFE_SETTLE_MS * 2);

	if (kind == 1) {          /* zero */
		for (int g = 0; g < GAIN_N; g++) {
			float dv = avg_dv(g, 64);
			g_cal.off_nA[g] = (dv / (float)rc.rf_ohm) * 1e9f * g_cal.scale;
		}
		store_save_cal(&g_cal);
		n = snprintk(line, sizeof(line),
			"{\"cal\":\"zero\",\"off\":[%.3f,%.3f,%.3f]}\n",
			(double)g_cal.off_nA[0], (double)g_cal.off_nA[1],
			(double)g_cal.off_nA[2]);
		tx_nus(line, n);
	} else {                  /* scale (기지저항) */
		float i_exp = (BIAS_MV * 1e6f) / ((float)kohm * 1000.0f); /* nA */
		float dv_exp = i_exp * (float)rc.rf_ohm / 1e9f;           /* V */
		/* 기대 ΔV 가 FS 의 60% 이내인 최고감도 게인 자동 선택 */
		static const float fs_v[GAIN_N] = { 0.15f, 0.6f, 1.2f };
		int g = GAIN_N - 1;
		for (int i = 0; i < GAIN_N; i++) {
			if (dv_exp < 0.6f * fs_v[i]) {
				g = i;
				break;
			}
		}
		float dv = avg_dv(g, 64);
		float i_raw = (dv / (float)rc.rf_ohm) * 1e9f - g_cal.off_nA[g] / g_cal.scale;
		if (i_raw > 0.01f) {
			g_cal.scale = i_exp / i_raw;
			g_cal.scale = CLAMP(g_cal.scale, 0.5f, 2.0f);
			store_save_cal(&g_cal);
		}
		n = snprintk(line, sizeof(line),
			"{\"cal\":\"scale\",\"exp_nA\":%.1f,\"meas_nA\":%.1f,\"scale\":%.4f}\n",
			(double)i_exp, (double)i_raw, (double)g_cal.scale);
		tx_nus(line, n);
	}

	afe_power(false);
	LOG_INF("calibration done (kind=%u)", kind);
}

/* ===================== 측정 스레드 ===================== */
static void meas_thread(void)
{
	while (1) {
		k_sem_take(&start_sem, K_FOREVER);

		if (cal_req) {                       /* 캘리브레이션 요청 */
			uint8_t kind = cal_req;
			cal_req = 0;
			do_calibration(kind, cal_kohm);
			atomic_set(&g_state, PSTAT_IDLE);
			status_req = true;
			continue;
		}

		k_mutex_lock(&cfg_lock, K_FOREVER);
		rc = g_cfg;   /* 스냅샷 */
		k_mutex_unlock(&cfg_lock);
		if (rc.rate_hz == 0) {
			rc.rate_hz = 1;
		}
		if (rc.oversample == 0) {
			rc.oversample = 1;
		}
		LOG_INF("START mode=%u rate=%uHz auto=%d rf=%u os=%u dur=%us on=%us off=%us cyc=%u",
			rc.mode, rc.rate_hz, (int)rc.autorange, rc.rf_ohm,
			rc.oversample, rc.duration_s, rc.on_s, rc.off_s, rc.cycles);
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

static void ble_set_fast(bool fast)
{
	struct bt_conn *c = conn_get();
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
		if (c.hw_changed) {
			store_save_hw(&c.cfg);   /* rf/os 는 NVM 영속화 */
			LOG_INF("cmd: config (rf=%u os=%u saved)", c.cfg.rf_ohm,
				c.cfg.oversample);
		} else {
			LOG_INF("cmd: config");
		}
		break;
	case CMD_START:
		k_mutex_lock(&cfg_lock, K_FOREVER);
		g_cfg = c.cfg;
		k_mutex_unlock(&cfg_lock);
		if (c.hw_changed) {
			store_save_hw(&c.cfg);
		}
		if (low_batt) {
			LOG_WRN("cmd: start 거부 (저전압 %dmV)", g_vbat_mv);
			status_req = true;
			break;
		}
		if (atomic_cas(&g_state, PSTAT_IDLE, PSTAT_RUN)) {
			stop_req = false;
			k_sem_give(&start_sem);
			LOG_INF("cmd: start");
		} else {
			LOG_WRN("cmd: start 무시 (이미 실행중 — stop 먼저)");
		}
		break;
	case CMD_ZERO:
	case CMD_CAL:
		if (atomic_cas(&g_state, PSTAT_IDLE, PSTAT_RUN)) {
			cal_req = (c.type == CMD_ZERO) ? 1 : 2;
			cal_kohm = c.cal_kohm;
			stop_req = false;
			k_sem_give(&start_sem);
			LOG_INF("cmd: cal (kind=%u kohm=%u)", cal_req, cal_kohm);
		} else {
			LOG_WRN("cmd: cal 무시 (측정중 — stop 먼저)");
		}
		break;
	case CMD_STATUS:
		status_req = true;
		break;
	default:
		break;
	}
}

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
	struct bt_conn *c = conn_get();
	if (!c) {
		return -ENOTCONN;
	}
	int err = bt_nus_send(c, data, len);
	bt_conn_unref(c);
	return err;
}

/* ===================== 데이터/상태 전송 ===================== */
#define BATCH_MAX   5
#define DRAIN_ITERS 8

static void tx_status(void)
{
	char line[224];
	const char *st = "idle";
	uint8_t mode;
	uint16_t rate;
	uint32_t rf;
	uint16_t os;

	enum pstat_state s = (enum pstat_state)atomic_get(&g_state);
	if (s == PSTAT_RUN) {
		st = "run";
	} else if (s == PSTAT_CYCLE_REST) {
		st = "rest";
	}
	k_mutex_lock(&cfg_lock, K_FOREVER);
	mode = rc.mode;
	rate = rc.rate_hz;
	rf   = g_cfg.rf_ohm;
	os   = g_cfg.oversample;
	k_mutex_unlock(&cfg_lock);

	int n = snprintk(line, sizeof(line),
		"{\"st\":\"%s\",\"mode\":%u,\"rate\":%u,\"cyc\":%u,\"range\":%d,"
		"\"pend\":%u,\"buf\":%u,\"gap\":%d,"
		"\"vbat\":%d,\"chg\":%d,\"qi\":%d,\"lb\":%d,\"rf\":%u,\"os\":%u}\n",
		st, mode, rate, run_cycle, cur_range,
		databuf_pending(), databuf_unacked(), (int)databuf_gap(),
		g_vbat_mv, (int)meas_charging(), (int)meas_qi_present(),
		(int)low_batt, rf, os);
	tx_nus(line, n);
}

/* 배터리 감시: 경고/컷오프. 컷오프 시 측정 정지→플러시 대기→System OFF */
static void battery_check(void)
{
	int mv = meas_vbat_mv();
	if (mv < 0) {
		return;
	}
	g_vbat_mv = mv;
	bool lb = (mv < VBAT_WARN_MV);
	if (lb != low_batt) {
		low_batt = lb;
		status_req = true;
		LOG_WRN("battery %dmV (%s)", mv, lb ? "LOW" : "ok");
	}
	if (mv < VBAT_CUT_MV && !meas_charging()) {
		LOG_ERR("battery cutoff (%dmV) — flush 후 System OFF", mv);
		stop_req = true;
		/* 남은 데이터 플러시 기회 (연결돼 있으면 main loop 가 전송) */
		for (int i = 0; i < 100 && databuf_pending(); i++) {
			k_msleep(100);
		}
		afe_power(false);
		led_set(false);
		sys_poweroff();   /* 복귀 = 리셋(충전 후 전원 재투입) */
	}
}

int main(void)
{
	int err;

	LOG_INF("=== discrete potentiostat (nRF52832 SAADC, app-controlled) ===");

	if (meas_init() != 0) {
		LOG_ERR("meas init failed");
		return 0;
	}
	store_init(&g_cfg, &g_cal);   /* NVM 에서 rf/os/cal 로드 */

	err = bt_enable(NULL);
	if (err) {
		LOG_ERR("bt_enable failed (%d)", err);
		return 0;
	}
	bt_nus_init(&nus_cb);
	start_advertising();
	battery_check();
	LOG_INF("ready. rf=%u os=%u scale=%.4f vbat=%dmV",
		g_cfg.rf_ohm, g_cfg.oversample, (double)g_cal.scale, g_vbat_mv);

	uint32_t tick = 0;
	char line[256];
	struct pstat_sample batch[BATCH_MAX];

	while (1) {
		k_msleep(20);
		tick++;

		/* 배터리: 5초마다 (측정 중에도 — SAADC 채널 분리라 간섭 없음) */
		if ((tick % (VBAT_PERIOD_MS / 20)) == 0) {
			battery_check();
		}
		/* 유휴+미연결: LED 짧은 블링크 (광고 표시) */
		if (!current_conn && atomic_get(&g_state) == PSTAT_IDLE) {
			led_set((tick % 100) < 3);   /* 2초마다 60ms */
		}

		if (!current_conn || !notif_enabled) {
			if ((tick % 250) == 0) {
				LOG_INF("alive up=%us state=%ld pend=%u buf=%u vbat=%d conn=%d",
					k_uptime_get_32() / 1000U, atomic_get(&g_state),
					databuf_pending(), databuf_unacked(),
					g_vbat_mv, current_conn ? 1 : 0);
			}
			continue;
		}

		/* 데이터 드레인 (무손실: 전송 성공한 배치만 절대위치 commit) */
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
				continue;
			}
			if (tx_nus(line, o) == 0) {
				databuf_commit_sent(base, k);
			} else {
				break;
			}
		}

		if (status_req || (tick % 50) == 0) {
			status_req = false;
			tx_status();
		}
	}
	return 0;
}
