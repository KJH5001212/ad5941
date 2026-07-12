/*
 * meas.h — SAADC 측정 엔진 + AFE 전원 + 배터리/충전 모니터
 * ------------------------------------------------------------
 * 하드웨어 (사용자 회로도 확정 핀맵 — app.overlay 와 일치):
 *   AIN0 = P0.02 = TIA_OUT      (차동 +)   ΔV = I_WE × RF
 *   AIN1 = P0.03 = VREF 1.024 V (차동 −)
 *   AIN2 = P0.04 = VBAT × 1/2   (1 MΩ:1 MΩ 분압)
 *   AIN3 = P0.05 = Charge = MCP73832_VDD × 1/2 (Qi 패드 감지, 패드 위 ≈2.5 V)
 *   P0.26 = AVDD 직접 급전 (high-drive, REF35102 + LPV802 ~2 µA)
 *   P0.08 = /CHG (MCP73832 STAT, 오픈드레인+100k 풀업, LOW=충전중)
 *   P0.06 = LED (옵션, 기본 미실장)
 */
#ifndef MEAS_H_
#define MEAS_H_

#include <stdint.h>
#include <stdbool.h>

int  meas_init(void);

/* AFE(레퍼런스+앰프) 전원. on 후 안정화 ~50 ms 는 호출측에서 대기. */
void afe_power(bool on);

/* SAADC 게인 전환 (0..GAIN_N-1). 다음 read 에서 재캘리브레이션 수행. */
int  meas_set_gain(int gain_idx);

/* 한 번의 HW 오버샘플 변환 → ΔV [V] (TIA_OUT − VREF).
 * oversample 은 2^n 으로 절사(1..256). 실패 시 음수 반환. */
int  meas_read_dv(uint16_t oversample, float *dv);

/* 현재 게인의 풀스케일 [V] (±FS) */
float meas_fs_volts(void);

/* 배터리 전압 [mV] (분압 ×2 반영). 실패 시 음수. */
int  meas_vbat_mv(void);

/* Qi 충전패드 위에 있으면(무선 5V 수신 중) true / 원시값 [mV] */
bool meas_qi_present(void);
int  meas_qi_mv(void);

/* 충전 중이면 true (MCP73832 STAT LOW) */
bool meas_charging(void);

void led_set(bool on);

#endif /* MEAS_H_ */
