/*
 * cmd.c — 소형 JSON 명령 파서 (고정 스키마 전용, 범용 아님)
 *  우리 명령 집합만 안전하게 뽑아내는 최소 구현.
 */
#include "cmd.h"
#include <zephyr/sys/util.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* "key" 뒤 콜론 다음 값의 시작 포인터 반환 (없으면 NULL) */
static const char *jfind(const char *buf, const char *key)
{
	char pat[24];
	snprintf(pat, sizeof(pat), "\"%s\"", key);
	const char *p = strstr(buf, pat);
	if (!p) {
		return NULL;
	}
	p += strlen(pat);
	while (*p && *p != ':') {
		p++;
	}
	if (*p != ':') {
		return NULL;
	}
	p++;
	while (*p == ' ' || *p == '\t') {
		p++;
	}
	return p;
}

static bool jint(const char *buf, const char *key, long *out)
{
	const char *p = jfind(buf, key);
	if (!p) {
		return false;
	}
	char *end = NULL;
	long v = strtol(p, &end, 10);
	if (end == p) {
		return false;
	}
	*out = v;
	return true;
}

static bool jstr(const char *buf, const char *key, char *out, size_t n)
{
	const char *p = jfind(buf, key);
	if (!p || *p != '"') {
		return false;
	}
	p++;
	size_t i = 0;
	while (*p && *p != '"' && i < n - 1) {
		out[i++] = *p++;
	}
	out[i] = '\0';
	return true;
}

bool cmd_parse(const char *json, size_t len, struct cmd *out,
	       const struct pstat_config *base)
{
	char buf[192];
	size_t n = (len < sizeof(buf) - 1) ? len : sizeof(buf) - 1;
	memcpy(buf, json, n);
	buf[n] = '\0';

	char c[16];
	if (!jstr(buf, "cmd", c, sizeof(c))) {
		return false;
	}

	memset(out, 0, sizeof(*out));
	long v;

	if (!strcmp(c, "ack")) {
		out->type = CMD_ACK;
		if (jint(buf, "seq", &v)) {
			out->ack_seq = (uint32_t)v;
		}
		return true;
	}
	if (!strcmp(c, "stop")) {
		out->type = CMD_STOP;
		return true;
	}
	if (!strcmp(c, "status")) {
		out->type = CMD_STATUS;
		return true;
	}
	if (!strcmp(c, "cal")) {
		out->type = CMD_CAL;
		return true;
	}
	if (!strcmp(c, "config") || !strcmp(c, "start")) {
		out->type = strcmp(c, "start") ? CMD_CONFIG : CMD_START;
		out->cfg = *base;   /* 미지정 필드는 현재값 유지 */

		if (jint(buf, "rate", &v)) {
			out->cfg.rate_hz = (uint16_t)CLAMP(v, 1, 100);
		}
		char m[12];
		if (jstr(buf, "mode", m, sizeof(m))) {
			if (!strcmp(m, "continuous")) {
				out->cfg.mode = PSTAT_MODE_CONTINUOUS;
			} else if (!strcmp(m, "timed")) {
				out->cfg.mode = PSTAT_MODE_TIMED;
			} else if (!strcmp(m, "cycle")) {
				out->cfg.mode = PSTAT_MODE_CYCLE;
			}
		}
		/* 초 값 상한: *1000(ms) 가 uint32 를 넘지 않게 (49.7일 한계 아래) */
		if (jint(buf, "dur", &v)) {
			out->cfg.duration_s = (uint32_t)CLAMP(v, 0, 4000000);
		}
		if (jint(buf, "on", &v)) {
			out->cfg.on_s = (uint32_t)CLAMP(v, 0, 4000000);
		}
		if (jint(buf, "off", &v)) {
			out->cfg.off_s = (uint32_t)CLAMP(v, 0, 4000000);
		}
		if (jint(buf, "cycles", &v)) {
			out->cfg.cycles = (uint32_t)MAX(v, 0);
		}
		if (jint(buf, "auto", &v)) {
			out->cfg.autorange = (v != 0);
		}
		if (jint(buf, "range", &v)) {
			out->cfg.range_idx = (uint8_t)MAX(v, 0);
		}
		return true;
	}
	return false;
}
