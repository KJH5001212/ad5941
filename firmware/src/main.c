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
/* 유휴 간격을 측정 간격과 동일(빠름)하게 유지한다. 느린 유휴 간격(250ms~1.5s)은
 * (1) stop 후 다음 start 응답이 느려지고(느림↔빠름 전환 지연),
 * (2) 유휴 중 이벤트 몇 개만 놓쳐도 supervision timeout 으로 끊겼다.
 * 상시 빠른 간격이면 전환 지연 없음 + 이벤트 여유 커서 끊김도 감소. 유휴 라디오
 * 전력은 조금 오르지만 AFE 런 전력에 비하면 미미. */
#define CONN_REST_MIN  24       /* 30ms (= FAST) */
#define CONN_REST_MAX  40       /* 50ms (= FAST) */
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

/* 실측 RTIA(Ω). sample_once 는 이 값으로 전류를 계산한다.
 * 우선순위: (1) 부팅 시 공칭 × RTIA_TRIM (5.1MΩ 1% 기준셀 1점 실측 보정)
 *          (2) RCAL 자동캘 — 재현성 게이트(cal_healthy)를 통과할 때만 덮어씀.
 * 벤더링된 ad5940lib 의 LPRtiaCal 은 구버전 버그 의심(ADI CHANGELOG v0.1.2/0.1.4 에
 * LPTIA 캘 수정 이력, 포럼에 동일 증상 보고) → 신뢰 못 하므로 게이트 필수. */
static float rtia_ohm[RTIA_N];

/* 5.1MΩ 1% 더미셀 1점 보정 트림 (2026-07-15 실측, 폐루프 바이어스 500mV 확정 후).
 * 기준전류 98.04nA(=500mV/5.1M) 대비 각 레인지 평균 읽음값의 비 = 실효 RTIA/공칭.
 * 게인 오차(RTIA 공차+ADC 게인)를 통째로 흡수한다. 각 평균은 240샘플(SEM ~1%). */
static const float RTIA_TRIM[RTIA_N] = {
	1.0459f,   /* 512k — 실측 102.54nA */
	1.0735f,   /* 256k — 실측 105.25nA */
	1.0874f,   /* 128k — 실측 106.61nA */
	1.0948f,   /*  64k — 실측 107.34nA */
	1.0991f,   /*  32k — 실측 107.76nA */
	1.0990f,   /*  16k — 실측 107.74nA */
	1.0917f,   /*   8k — 실측 107.03nA */
	1.09f,     /*   4k — 외삽 (32k~8k 추세 평탄 ~1.09) */
	1.09f,     /*   2k — 외삽 */
	1.09f,     /*   1k — 외삽 */
	1.0f,      /* 110Ω — 미측정 (98nA 신호 ~2LSB 라 측정 불가), 공칭 유지 */
};

#define RCAL_OHM  1.0e6f   /* R5: RCAL0-RCAL1 간 실물 1MΩ 1% (2026-07-16 재실장 —
			    * 이전 100k/510k 는 엉뚱한 패드에 실장돼 무효였음).
			    * 캘 정확도 = 이 저항의 정확도. 교체 시 이 값도 수정. */

/* ===================== 진단 전역 (SWD로 직접 읽기) ===================== */
volatile uint32_t g_adiid[4];
volatile float    g_rtia_cal[RTIA_N];   /* 캘 결과 확인용 (SWD) */
volatile int32_t  g_cal_ret[RTIA_N];    /* LPRtiaCal 반환코드 (진단) */
volatile float    g_rtia_raw[RTIA_N];   /* 캘 원시 magnitude (거부 전, 진단) */
volatile float    g_bias_mv = -1.0f;    /* ADC 로 자가측정한 실제 셀 바이어스(mV) */
#define CAL_EXP_N 6
volatile int32_t  g_cal_exp[CAL_EXP_N]; /* 캘 재현성 실험: 512k 연속 6회 결과(Ω) */

/* 온디맨드 캘 요청({"cmd":"cal"})과 결과 보고 플래그.
 * 캘은 부팅에서 돌리지 않는다 — lib 캘이 AFE 삐끗 시 행/장시간 소요 위험이
 * 있어(타임아웃 패치로 완화했지만) 부팅은 빠르고 안전하게 유지. */
static volatile bool cal_req;
static volatile bool cal_report_req;
volatile int32_t  g_dac12_trim;         /* 폐루프 바이어스 트림 (12bit DAC 코드 오프셋) */

/* Vbias(RE) 12bit DAC 코드 = 공칭 + 폐루프 트림. 실측에서 바이어스가 +23%(614mV)
 * 나와 부팅 시 자가측정으로 코드를 보정해 실제 500mV 에 맞춘다. */
static int16_t dac12_trim;

static uint16_t dac12_code(void)
{
	int32_t c = (int32_t)((VZERO_MV - CELL_BIAS_MV - 200.0f) / DAC12_LSB_MV + 0.5f)
		    + dac12_trim;
	return (uint16_t)CLAMP(c, 0, 4095);
}
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

/* RCAL(R5=10MΩ 1%)로 내부 RTIA 각 레인지를 DC 자가보정 → rtia_ohm[] 에 실측값
 * 저장. 내부 RTIA ±20% 공차를 제거해 전류 정확도를 맞춘다. RCAL(10M)이 작은
 * RTIA 와 크게 어긋나는 조합은 캘이 부정확할 수 있어, 공칭의 0.5~2배를 벗어난
 * 결과는 버리고 공칭을 유지한다. */
static bool cal_healthy;   /* RCAL 자동캘 신뢰 가능 여부 (재현성 게이트 통과) */

/* 한 레인지 LPRtiaCal 실행. ADI 공식 예제(Amperometric) 레시피 그대로:
 *  - AC 캘 필수 (DC 는 ADC/PGA 선행 캘 필요 — ADI 주석 명시. 실제로 랜덤값)
 *  - fFreq 는 SINC2(OSR22)+DFT(2048) 체인의 bin 에 정렬: AdcClk/4/22/2048*3 ≈266.3Hz
 * 반환: magnitude(Ω), 실패 시 -1. */
static float lprtia_cal_range_f(int i, float freq, float frcal)
{
	LPRTIACal_Type cal;
	fImpPol_Type res;

	AD5940_StructInit(&cal, sizeof(cal));
	cal.LpAmpSel     = LPAMP0;
	cal.fFreq        = freq;
	cal.fRcal        = frcal;
	cal.LpTiaRtia    = RTIA_TAB[i].sel;
	cal.LpAmpPwrMod  = LPAMPPWR_NORM;
	cal.bWithCtia    = bFALSE;
	cal.ADCSinc3Osr  = ADCSINC3OSR_4;
	cal.ADCSinc2Osr  = ADCSINC2OSR_22;
	cal.DftCfg.DftNum   = DFTNUM_2048;
	cal.DftCfg.DftSrc   = DFTSRC_SINC2NOTCH;
	cal.DftCfg.HanWinEn = bTRUE;
	cal.SysClkFreq   = 16000000.0f;
	cal.AdcClkFreq   = 16000000.0f;
	cal.bPolarResult = bTRUE;

	res.Magnitude = -1.0f;
	int32_t err = AD5940_LPRtiaCal(&cal, &res);
	g_cal_ret[i]  = err;              /* 진단: 반환코드 */
	g_rtia_raw[i] = res.Magnitude;    /* 진단: 원시 결과(적용여부 무관) */
	return (err == AD5940ERR_OK) ? res.Magnitude : -1.0f;
}

/* 기본 캘 주파수: SINC2(OSR22)+DFT(2048) bin 정렬, 신호 3주기 ≈266.3Hz */
#define CAL_FREQ_3P  (16000000.0f / 4 / 22 / 2048 * 3)
#define CAL_FREQ_1P  (16000000.0f / 4 / 22 / 2048)      /* 1주기 ≈88.8Hz */

static float lprtia_cal_range(int i)
{
	/* 큰 RTIA(512k/256k/128k)는 fRcal 을 절반 RTIA 로 "낮춰 전달"해
	 * 여기전압을 800mVpp 로 제한한다(클램프 미달) → lib 이 PGA1.5/2 를
	 * 선택 → PGA 포화 제거(1M RCAL 에서 256k 가 PGA9 90%FS 로 -10%
	 * 압축되던 문제). 결과는 실제 RCAL 로 재스케일:
	 * result_lib = RTIA×fake/RCAL_실물 → ×(RCAL_OHM/fake) 로 복원. */
	float fake = RCAL_OHM;
	if (i <= 1) {
		fake = RTIA_TAB[i].ohm * 0.5f;
	} else if (i == 2) {
		fake = RTIA_TAB[i].ohm * 0.35f;   /* 128k: V_rcal 압축(+7%) 줄이기 */
	}
	float m = lprtia_cal_range_f(i, CAL_FREQ_3P, fake);
	return (m > 0.0f) ? m * (RCAL_OHM / fake) : m;
}

/* RCAL(1M) 자동캘 — 하이브리드 적용.
 * 실측 결과(2026-07-16): 1M RCAL 에서 작은 RTIA(16k 이하)는 캘이 정확
 * (1k +0.7%, 4k -2.8%, 16k -5.6%)하지만, 큰 RTIA(512k 등)는 캘 신호가
 * 크고(±488mVpp) lib 의 PGA 선택 버그(자체 과소추정 → 과대게인)로 PGA
 * 비선형 영역을 넘나들어 폭주(±134%). 따라서:
 *  - 512k~32k (idx 0~4, nA 측정용): 물리 트림 유지 — 캘 절대 미적용
 *  - 16k~110Ω (idx 5~10, µA 영역): 게이트 통과 시 캘 적용 (트림이 외삽/
 *    미측정이던 영역이라 캘이 더 정확)
 * 게이트 = 1k(idx 9) 2회 재현성 <3% + 공칭 0.6~1.6배 타당성. */
#define CAL_APPLY_MIN 5    /* 캘 적용 시작 인덱스 (16k) */
#define CAL_GATE_IDX  9    /* 게이트 판정 레인지 (1k — 캘 최적 영역) */

/* 캘 결과 적용 스위치. 1 = 게이트 통과 시 16k~110Ω 에 적용.
 * (그간의 캘 실패는 전부 하드웨어였음: R5 아닌 엉뚱한 패드 실장 + 플럭스
 * 누설. 올바른 패드에 1M 1% + 세척 후: 1k 재현성 1.9%, 512k 캘이 물리
 * 트림과 0.14% 일치로 교차검증 완료 — 2026-07-16.) */
#define CAL_APPLY_ENABLE 1

static void ad5941_rtia_cal(void)
{
	/* 웜업: 첫 4~6회 캘은 HS 루프/레퍼런스 미안정으로 크게 튐(실측 재현).
	 * 버리는 실행으로 안정화시킨 뒤 판정에 들어간다. */
	for (int r = 0; r < 4; r++) {
		(void)lprtia_cal_range(CAL_GATE_IDX);
		k_msleep(30);
	}

	/* 재현성 진단: 1k 연속 6회 (BLE cal 프레임의 "exp").
	 * 게이트 = 6회 전체 스프레드 <10% + 평균이 공칭 0.6~1.6배.
	 * (단일 런 노이즈 ±2~3% 라 "앞 2회 <3%" 는 경계선 동전던지기였음.
	 *  정상 하드웨어 스프레드 3~6%, 고장 시절 18~185% — 10% 로 완벽 분리.) */
	float mn = 1e30f, mx = -1e30f, sum = 0.0f;
	for (int r = 0; r < CAL_EXP_N; r++) {
		float v = lprtia_cal_range(CAL_GATE_IDX);
		g_cal_exp[r] = (int32_t)v;
		if (v < mn) mn = v;
		if (v > mx) mx = v;
		sum += v;
		k_msleep(30);
	}
	float avg = sum / CAL_EXP_N;
	float nom = RTIA_TAB[CAL_GATE_IDX].ohm;

	cal_healthy = (CAL_APPLY_ENABLE && mn > 0.0f &&
		       (mx - mn) < 0.10f * avg &&
		       avg > 0.6f * nom && avg < 1.6f * nom);

	/* 전 레인지 적용 (2회 실행 일치 시 평균). 기준은 레인지 클래스별:
	 *  - 큰 레인지(512k~32k, i<CAL_APPLY_MIN): 물리 트림(0.2% 검증)이 기준점.
	 *    캘이 3% 이내로 재현되고 트림의 ±15% 안일 때만 대체 — 아니면 트림 유지.
	 *  - 작은 레인지(16k~110Ω): 8% 일치 + 공칭 0.6~1.6배 (트림이 외삽이던 영역). */
	for (int i = 0; i < RTIA_N; i++) {
		float m1 = lprtia_cal_range(i);
		float m2 = lprtia_cal_range(i);
		float d = (m1 > m2) ? m1 - m2 : m2 - m1;
		bool ok;
		if (i < CAL_APPLY_MIN) {
			/* 큰 레인지: 트림 ±8% — 온도추적은 허용, 캘 계통오차는 차단 */
			ok = (m1 > 0.0f && m2 > 0.0f && d < 0.03f * m1 &&
			      m1 > 0.92f * rtia_ohm[i] && m1 < 1.08f * rtia_ohm[i]);
		} else {
			ok = (m1 > 0.0f && m2 > 0.0f && d < 0.08f * m1 &&
			      m1 > 0.6f * RTIA_TAB[i].ohm && m1 < 1.6f * RTIA_TAB[i].ohm);
		}
		if (cal_healthy && ok) {
			rtia_ohm[i] = 0.5f * (m1 + m2);
		}
		g_rtia_cal[i] = rtia_ohm[i];   /* 진단(SWD) */
	}
	LOG_INF("RTIA cal %s: 16k=%.0f 1k=%.0f (Ω)",
		cal_healthy ? "APPLIED(16k~110)" : "diag-only",
		(double)rtia_ohm[5], (double)rtia_ohm[9]);
}

static float measure_bias_mv(void);   /* 아래 정의 (AFE 헬퍼 뒤) */
static bool bias_trimmed;

/* 폐루프 바이어스 트림 (1회): 실측(Vzero-Vbias 핀)이 CELL_BIAS_MV 에 수렴하도록
 * 12bit DAC 코드를 보정. 실측 +23%(614mV) 오차 해결. DAC 실제 기울기가 공칭
 * LSB 와 ~20% 다를 수 있어 0.7 감쇠로 진동 없이 수렴. 부팅과 start 경로 양쪽에서
 * 호출 — 부팅 때 AFE 가 죽어 있다가 start 재초기화로 살아난 경우에도 트림 보장. */
static void bias_trim_once(void)
{
	if (bias_trimmed || g_adiid[0] != AD5940_ADIID) {
		return;
	}
	for (int it = 0; it < 8; it++) {
		float mv = measure_bias_mv();
		if (mv < 0.0f) {
			return;   /* 측정 실패 → 다음 기회에 재시도 */
		}
		float err = mv - CELL_BIAS_MV;
		if (err < 2.0f && err > -2.0f) {
			break;   /* ±2mV 수렴 */
		}
		/* bias = Vzero - Vbias → 바이어스가 크면 Vbias 코드를 올린다 */
		dac12_trim += (int16_t)(0.7f * err / DAC12_LSB_MV
					+ (err > 0 ? 0.5f : -0.5f));
	}
	g_bias_mv = measure_bias_mv();   /* 최종 수렴값으로 보고 */
	g_dac12_trim = dac12_trim;
	bias_trimmed = true;
	LOG_INF("bias self-measure: %.1f mV (dac12_trim=%d)",
		(double)g_bias_mv, dac12_trim);
}

/* LP loop: RTIA 선택 + 바이어스 on/off 파라미터화 */
static void afe_lploop_cfg(uint8_t rtia_sel, bool bias_on)
{
	LPLoopCfg_Type lp;

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
	lp.LpDacCfg.DacData12Bit   = dac12_code();   /* 폐루프 트림 반영 */

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
	filt.ADCSinc2Osr         = ADCSINC2OSR_1333;   /* 150Hz — 50/60Hz 노치 지원 OSR */
	filt.ADCAvgNum           = ADCAVGNUM_16;
	filt.BpSinc3             = bFALSE;
	filt.BpNotch             = bFALSE;   /* 50/60Hz 노치 활성 — 전원험 제거.
					      * (bTRUE 바이패스 시절: 5.1M 노드 험 버스트가
					      * 측정창 2개에 상보 쌍(±90nA)으로 새어들었음) */
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

/* 실제 셀 바이어스(V(Vzero0)-V(Vbias0), mV)를 ADC 로 자가측정.
 * LPDAC 양자화/게인 오차를 포함한 실측값 → 기준셀 전류(I=V/R) 계산과
 * "전류 오차가 바이어스 탓인지 RTIA 탓인지" 분리에 사용. 부팅 시 1회. */
static float measure_bias_mv(void)
{
	ADCBaseCfg_Type adc;
	float sum = 0.0f;
	int cnt = 0;

	afe_lploop_cfg(RTIA_TAB[0].sel, true);   /* 바이어스 on */

	/* Vzero0/Vbias0 "핀"은 2PIN 스위치를 닫아야 DAC 출력이 나온다.
	 * (측정용으로만 잠시 라우팅 — afe_stop 의 lploop off 가 원복) */
	LPDACCfg_Type dac;
	AD5940_StructInit(&dac, sizeof(dac));
	dac.LpdacSel      = LPDAC0;
	dac.LpDacSrc      = LPDACSRC_MMR;
	dac.LpDacVzeroMux = LPDACVZERO_6BIT;
	dac.LpDacVbiasMux = LPDACVBIAS_12BIT;
	dac.LpDacRef      = LPDACREF_2P5;
	dac.DataRst       = bFALSE;
	dac.PowerEn       = bTRUE;
	dac.LpDacSW       = LPDACSW_VBIAS2LPPA | LPDACSW_VZERO2LPTIA |
			    LPDACSW_VBIAS2PIN | LPDACSW_VZERO2PIN;
	dac.DacData6Bit   = (uint16_t)((VZERO_MV - 200.0f) / DAC6_LSB_MV + 0.5f);
	dac.DacData12Bit  = dac12_code();   /* 폐루프 트림 반영 */
	AD5940_LPDACCfgS(&dac);
	k_msleep(10);   /* 핀 세틀 */

	afe_adc_cfg();
	adc.ADCMuxP = ADCMUXP_VZERO0;    /* WE 동작점 (Vzero0 핀) */
	adc.ADCMuxN = ADCMUXN_VBIAS0;    /* RE (Vbias0 핀) → P-N = 셀 바이어스 */
	adc.ADCPga  = ADC_PGA;
	AD5940_ADCBaseCfgS(&adc);
	AD5940_AFECtrlS(AFECTRL_HPREFPWR | AFECTRL_ADCPWR, bTRUE);
	k_msleep(50);
	AD5940_INTCCfg(AFEINTC_1, AFEINTSRC_SINC2RDY, bTRUE);
	AD5940_INTCClrFlag(AFEINTSRC_SINC2RDY);
	AD5940_AFECtrlS(AFECTRL_ADCCNV, bTRUE);
	for (int i = 0; i < 22; i++) {
		if (!wait_sinc2()) {
			break;
		}
		uint32_t code = AD5940_ReadAfeResult(AFERESULT_SINC2);
		if (i >= 6) {   /* 파이프라인 프라이밍 폐기 */
			sum += AD5940_ADCCode2Volt(code, ADC_PGA, ADC_VREF);
			cnt++;
		}
	}
	afe_stop();
	return cnt ? (sum / cnt) * 1000.0f : -1.0f;
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
	float vmin = 1e30f, vmax = -1e30f;
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
		float v = AD5940_ADCCode2Volt(code, ADC_PGA, ADC_VREF);
		vsum += v;
		if (v < vmin) vmin = v;
		if (v > vmax) vmax = v;
		cnt++;
	} while ((int32_t)(k_uptime_get_32() - next_due) < 0);

	next_due += period_ms;
	/* 크게 밀렸으면(레인지 flush 등) 마감 재동기화 */
	if ((int32_t)(k_uptime_get_32() - next_due) > (int32_t)period_ms) {
		next_due = k_uptime_get_32() + period_ms;
	}

	/* 절사평균: 창 안의 최대/최소 1개씩 버림 — 단발 스파이크(험 버스트,
	 * 정전기 등)가 창 평균을 ±수십 nA 끌고가는 것 방지 (샘플 6개 이상일 때) */
	float vavg;
	if (cnt >= 6) {
		vavg = (vsum - vmin - vmax) / (float)(cnt - 2);
	} else {
		vavg = vsum / (float)cnt;
	}
	smp->current_nA = (vavg / rtia_ohm[cur_range]) * 1e9f;   /* 캘된 실측 RTIA 사용 */
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
	ble_set_fast(false);   /* 유휴=저전력 느린 간격 (전원 마진 확보) */
	LOG_INF("run finished (cycles=%u)", run_cycle);
}

/* ===================== 측정 스레드 ===================== */
static void meas_thread(void)
{
	while (1) {
		k_sem_take(&start_sem, K_FOREVER);
		/* AFE 상태를 가벼운 SPI 읽기로 재확인한다(리셋 아님).
		 *  - 살아있으면(0x4144) 그대로 진행 → 빠름, 무부하.
		 *  - 죽었을 때만 HWReset+재초기화로 되살린다(최대 3회) → 리플래시 없이
		 *    Start 만으로 복구하되, 매번 리셋하지 않아 재안정화 지연/전원부담 없음.
		 * (매 start 마다 무조건 HWReset 하면 재안정화로 느려지고 반복 리셋이
		 *  전원을 눌러 브라운아웃을 유발했다.) */
		g_adiid[0] = AD5940_ReadReg(REG_AFECON_ADIID);
		if (g_adiid[0] != AD5940_ADIID) {
			LOG_WRN("AFE 무응답(0x%04x) — 재초기화 시도", g_adiid[0]);
			int afe_try = 0;
			while (ad5941_afe_init() != 0 && ++afe_try < 3) {
				k_msleep(30);
			}
			if (g_adiid[0] != AD5940_ADIID) {
				LOG_ERR("AFE 재초기화 실패 — start/cal 거부");
				cal_req = false;
				atomic_set(&g_state, PSTAT_IDLE);
				status_req = true;
				continue;
			}
		}
		bias_trim_once();   /* 부팅 때 AFE 죽어 트림 못 했으면 여기서 (없인 +23%) */
		if (cal_req) {
			cal_req = false;
			ad5941_rtia_cal();       /* 온디맨드 RCAL 캘 (게이트 통과 시 적용) */
			cal_report_req = true;   /* 결과 BLE 진단 프레임 전송 */
			status_req = true;
			if (atomic_get(&g_state) != PSTAT_RUN) {
				continue;   /* 캘만 요청됨 — idle 유지 */
			}
			/* 캘 중 start 도착(세마포어 합쳐짐) → 이어서 측정 실행 */
		}
		k_mutex_lock(&cfg_lock, K_FOREVER);
		rc = g_cfg;   /* 스냅샷 */
		k_mutex_unlock(&cfg_lock);
		if (rc.rate_hz == 0) {
			rc.rate_hz = 1;   /* 0 나눗셈 방어 */
		}
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

	/* 재연결: 미ACK분 처음부터 재전송. 실행중이면 빠른 간격, 유휴면 느린 간격(저전력). */
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
		if (atomic_cas(&g_state, PSTAT_IDLE, PSTAT_RUN)) {
			stop_req = false;
			k_sem_give(&start_sem);
			LOG_INF("cmd: start");
		} else {
			LOG_WRN("cmd: start 무시 (이미 실행중 — stop 먼저)");
		}
		break;
	case CMD_STATUS:
		status_req = true;
		break;
	case CMD_CAL:
		if (atomic_get(&g_state) == PSTAT_IDLE) {
			cal_req = true;
			k_sem_give(&start_sem);
			LOG_INF("cmd: cal");
		} else {
			LOG_WRN("cmd: cal 무시 (측정 중 — stop 먼저)");
		}
		break;
	default:
		break;
	}
}

/* NUS notify 활성 콜백 */
static void nus_send_enabled(enum bt_nus_send_status status)
{
	notif_enabled = (status == BT_NUS_SEND_STATUS_ENABLED);
	if (notif_enabled) {
		cal_report_req = true;   /* 연결되면 캘 진단 프레임 1회 전송 */
	}
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

/* 캘 진단: 각 레인지의 LPRtiaCal 반환코드 + 원시 magnitude(Ω) 를 BLE 로 1회 전송.
 * SWD 대신 PC(bleak)로 읽어 auto-cal 실패 원인 분석. 형식: {"cal":[[i,ret,raw],...]} */
static void tx_caldbg(void)
{
	char line[360];   /* MTU 247 초과분은 bt_nus_send 가 청크 분할 */
	int o = snprintk(line, sizeof(line), "{\"cal\":[");
	for (int i = 0; i < RTIA_N; i++) {
		size_t rem = (o < (int)sizeof(line)) ? sizeof(line) - o : 0;
		o += snprintk(line + o, rem, "%s[%d,%d,%d]",
			i ? "," : "", i, (int)g_cal_ret[i], (int)g_rtia_raw[i]);
	}
	size_t rem = (o < (int)sizeof(line)) ? sizeof(line) - o : 0;
	o += snprintk(line + o, rem, "],\"bias_mv\":%d,\"applied\":%d,\"exp\":[",
		      (int)g_bias_mv, (int)cal_healthy);
	for (int i = 0; i < CAL_EXP_N; i++) {
		rem = (o < (int)sizeof(line)) ? sizeof(line) - o : 0;
		o += snprintk(line + o, rem, "%s%d", i ? "," : "", g_cal_exp[i]);
	}
	rem = (o < (int)sizeof(line)) ? sizeof(line) - o : 0;
	o += snprintk(line + o, rem, "]}\n");
	/* MTU(247)-3 보다 길 수 있으므로 200B 단위로 분할 전송 (수신측은 \n 재조립) */
	for (int off = 0; off < o; off += 200) {
		uint16_t n = (uint16_t)MIN(200, o - off);
		if (tx_nus((const uint8_t *)line + off, n) != 0) {
			break;
		}
	}
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
	/* 전류계산 RTIA = 공칭 × 실측 트림 (5.1MΩ 기준셀 1점 보정).
	 * 자동캘은 게이트 통과 시에만 이 값을 덮어쓴다. */
	for (int i = 0; i < RTIA_N; i++) {
		rtia_ohm[i] = RTIA_TAB[i].ohm * RTIA_TRIM[i];
	}
	/* AFE init 실패 시 최대 5회 재시도(HWReset 반복). 마진 부족 부팅에서
	 * AFE 가 한 번에 안 올라와도 되살아날 확률을 높인다. */
	int boot_try = 0;
	while (ad5941_afe_init() != 0 && ++boot_try < 5) {
		LOG_WRN("AFE init 실패, 재시도 %d/5", boot_try);
		k_msleep(50);
	}
	if (g_adiid[0] != AD5940_ADIID) {
		LOG_WRN("ADIID mismatch — BLE 는 올리되 측정 불가");
	} else {
		bias_trim_once();   /* 폐루프 바이어스 트림 → 실제 500mV */
		/* RCAL 자동캘은 부팅에서 안 돌린다 — {"cmd":"cal"} 로 온디맨드 실행 */
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

		/* 연결 직후 캘 진단 1회 전송 (PC 로 auto-cal 반환코드/원시값 분석) */
		if (cal_report_req) {
			cal_report_req = false;
			tx_caldbg();
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
