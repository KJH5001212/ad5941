/*
 * cmd.h — BLE(NUS)로 받는 JSON 명령 파서
 *  명령 예:
 *   {"cmd":"start","mode":"cycle","rate":10,"on":5,"off":295,"cycles":0,"auto":1}
 *   {"cmd":"config",...}   {"cmd":"stop"}   {"cmd":"status"}
 *   {"cmd":"ack","seq":1234}
 *  전위는 펌웨어 고정(0.5V)이라 명령에 없음.
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
	CMD_CAL,     /* {"cmd":"cal"} — RCAL 자동캘 온디맨드 실행 (idle 에서만) */
};

struct cmd {
	enum cmd_type type;
	struct pstat_config cfg; /* CONFIG/START: base 에서 시작해 지정 필드만 덮음 */
	uint32_t ack_seq;        /* ACK */
};

/* JSON 한 줄 파싱. base=현재 설정(미지정 필드 기본값). 성공 시 true. */
bool cmd_parse(const char *json, size_t len, struct cmd *out,
	       const struct pstat_config *base);

#endif /* CMD_H_ */
