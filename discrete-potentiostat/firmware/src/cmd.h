/*
 * cmd.h — BLE(NUS) JSON 명령 파서 (AD5941 버전과 프로토콜 호환 + 확장)
 *  기존 명령 (앱 무수정 동작):
 *   {"cmd":"start","mode":"cycle","rate":10,"on":5,"off":295,"cycles":0,"auto":1}
 *   {"cmd":"config",...}   {"cmd":"stop"}   {"cmd":"status"}   {"cmd":"ack","seq":N}
 *  확장 (측정 파라미터 — config/start 에 추가 필드로):
 *   "rf":1000000      TIA RF [Ω] (보드에서 교체 시 알려줌, NVM 저장)
 *   "os":64           SAADC 하드웨어 오버샘플 (1..256, 2^n 절사, NVM 저장)
 *   "auto":0,"range":2  게인 수동 고정 (0=±150nA … 2=±2.1µA @1MΩ)
 *  확장 (캘리브레이션):
 *   {"cmd":"zero"}              셀 오픈 상태에서 게인별 제로 오프셋 측정+저장
 *   {"cmd":"cal","kohm":1000}   기지저항 더미셀로 스케일 보정 (I=512mV/R)
 *  전위는 하드웨어 고정(+0.512V)이라 명령에 없음.
 */
#ifndef CMD_H_
#define CMD_H_

#include "pstat.h"
#include <stddef.h>

enum cmd_type {
	CMD_NONE = 0,
	CMD_CONFIG,
	CMD_START,
	CMD_STOP,
	CMD_ACK,
	CMD_STATUS,
	CMD_ZERO,      /* 제로(오프셋) 캘리브레이션 */
	CMD_CAL,       /* 스케일 캘리브레이션 (기지저항) */
};

struct cmd {
	enum cmd_type type;
	struct pstat_config cfg; /* CONFIG/START: base 에서 시작해 지정 필드만 덮음 */
	uint32_t ack_seq;        /* ACK */
	uint32_t cal_kohm;       /* CAL: 더미셀 저항 [kΩ] */
	bool     hw_changed;     /* rf/os 필드가 실제로 지정됨 → NVM 저장 필요 */
};

/* JSON 한 줄 파싱. base=현재 설정(미지정 필드 기본값). 성공 시 true. */
bool cmd_parse(const char *json, size_t len, struct cmd *out,
	       const struct pstat_config *base);

#endif /* CMD_H_ */
