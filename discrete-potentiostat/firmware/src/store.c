/*
 * store.c — settings/NVS 영속화 구현
 *  키: pstat/rf (u32), pstat/os (u16), pstat/cal (struct pstat_cal)
 */
#include "store.h"

#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/logging/log.h>
#include <string.h>

LOG_MODULE_REGISTER(store, LOG_LEVEL_INF);

static struct pstat_config *s_cfg;
static struct pstat_cal    *s_cal;

static int pstat_set(const char *name, size_t len, settings_read_cb read_cb,
		     void *cb_arg)
{
	if (!strcmp(name, "rf") && len == sizeof(uint32_t)) {
		uint32_t v;
		if (read_cb(cb_arg, &v, sizeof(v)) == sizeof(v) && v >= 10000 &&
		    v <= 100000000) {
			s_cfg->rf_ohm = v;
		}
		return 0;
	}
	if (!strcmp(name, "os") && len == sizeof(uint16_t)) {
		uint16_t v;
		if (read_cb(cb_arg, &v, sizeof(v)) == sizeof(v) && v >= 1 && v <= 256) {
			s_cfg->oversample = v;
		}
		return 0;
	}
	if (!strcmp(name, "cal") && len == sizeof(struct pstat_cal)) {
		struct pstat_cal c;
		if (read_cb(cb_arg, &c, sizeof(c)) == sizeof(c) &&
		    c.scale > 0.5f && c.scale < 2.0f) {   /* 온전성 검사 */
			*s_cal = c;
		}
		return 0;
	}
	return -ENOENT;
}

SETTINGS_STATIC_HANDLER_DEFINE(pstat, "pstat", NULL, pstat_set, NULL, NULL);

int store_init(struct pstat_config *cfg, struct pstat_cal *cal)
{
	s_cfg = cfg;
	s_cal = cal;
	int err = settings_subsys_init();
	if (err) {
		LOG_ERR("settings init failed (%d)", err);
		return err;
	}
	settings_load_subtree("pstat");
	LOG_INF("loaded: rf=%u os=%u scale=%.4f", cfg->rf_ohm, cfg->oversample,
		(double)cal->scale);
	return 0;
}

void store_save_hw(const struct pstat_config *cfg)
{
	settings_save_one("pstat/rf", &cfg->rf_ohm, sizeof(cfg->rf_ohm));
	settings_save_one("pstat/os", &cfg->oversample, sizeof(cfg->oversample));
}

void store_save_cal(const struct pstat_cal *cal)
{
	settings_save_one("pstat/cal", cal, sizeof(*cal));
}
