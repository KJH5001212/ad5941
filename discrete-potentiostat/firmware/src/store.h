/*
 * store.h — 설정/캘리브레이션 NVM 영속화 (Zephyr settings + NVS)
 *  RF/CF 교체 절차: ① 새 RF 납땜 ② 앱에서 {"cmd":"config","rf":...,"os":...}
 *  ③ 더미셀 {"cmd":"cal","kohm":...} + 셀 오픈 {"cmd":"zero"} — 리플래시 불필요.
 */
#ifndef STORE_H_
#define STORE_H_

#include "pstat.h"

/* settings 서브시스템 초기화 + 저장값 로드(cfg/cal 의 해당 필드 덮어씀) */
int  store_init(struct pstat_config *cfg, struct pstat_cal *cal);

/* rf_ohm/oversample 영속화 (config 명령으로 바뀔 때 호출) */
void store_save_hw(const struct pstat_config *cfg);

/* 캘리브레이션 영속화 (zero/cal 명령 후 호출) */
void store_save_cal(const struct pstat_cal *cal);

#endif /* STORE_H_ */
