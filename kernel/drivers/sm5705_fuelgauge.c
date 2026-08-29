// SPDX-License-Identifier: GPL-2.0
/*
 * Silicon Mitus SM5705 fuel gauge
 *
 * Samsung fit an SM5705 - a combined charger, fuel gauge and MUIC - on several
 * Qualcomm devices, in preference to using the Qualcomm PMIC for the battery.
 * On the Galaxy Tab S6 (SM-T860) the gauge sits at 0x71 on QUP se11 while the
 * charger (0x49) and MUIC (0x25) share se4. That matters on this device
 * because pm8150b, which would otherwise carry the charger and gauge, is not
 * reachable at all: the SPMI arbiter denies the applications processor every
 * register in that slave ID.
 *
 * There is no public datasheet. The register map here was established by
 * reading the live part against a known reference - a separately calibrated
 * ADC reading of the battery rail - and confirming that each value tracked
 * reality over time:
 *
 *   0x05  SOC          high byte whole percent, low byte 1/256ths
 *   0x06  OCV          raw / 2048 volts
 *   0x07  VBAT         raw / 2048 volts
 *   0x08  CURRENT      raw / 2048 amps, signed; positive while charging
 *
 * Voltage agreed with the reference to within 30 mV across every sample, SOC
 * climbed steadily while charging, and current read a plausible ~1.1 A. Only
 * registers that were confirmed this way are used; the rest of the map is left
 * alone rather than guessed at.
 */

#include <linux/devm-helpers.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mod_devicetable.h>
#include <linux/power_supply.h>
#include <linux/slab.h>
#include <linux/workqueue.h>

#define SM5705_FG_DEVICE_ID		0x00
#define SM5705_FG_SOC			0x05
#define SM5705_FG_OCV			0x06
#define SM5705_FG_VBAT			0x07
#define SM5705_FG_CURRENT		0x08

/* Volts and amps are fixed point with 11 fractional bits. */
#define SM5705_FG_FRACTIONAL_BITS	11

/*
 * Below this the current reading is treated as neither charging nor
 * discharging. The part reports a small non-zero current when idle, and
 * without a deadband the status flickers between charging and discharging.
 */
#define SM5705_FG_CURRENT_NOISE_UA	50000

/*
 * How often to look for a change in charging state.
 *
 * The gauge interrupt line is described in the vendor device tree but is not
 * wired up here, and nothing else on this board reports cable events, so
 * without polling the desktop only notices a plug or unplug at its own leisure
 * - upower re-reads on a timer measured in tens of seconds. Five seconds is
 * frequent enough that pulling the charger updates while you are still looking
 * at the screen, and two I2C word reads on a 400 kHz bus every five seconds
 * costs nothing.
 */
#define SM5705_FG_POLL_INTERVAL		(5 * HZ)

struct sm5705_fg {
	struct i2c_client *client;
	struct power_supply *psy;
	struct power_supply_battery_info *info;
	struct delayed_work poll_work;
	int last_status;
};

static int sm5705_fg_read(struct sm5705_fg *fg, u8 reg)
{
	int ret = i2c_smbus_read_word_data(fg->client, reg);

	if (ret < 0)
		dev_err(&fg->client->dev, "read of reg 0x%02x failed: %d\n",
			reg, ret);

	return ret;
}

/* raw / 2048, scaled to micro-units, without losing the fraction. */
static int sm5705_fg_to_micro(int raw)
{
	return ((long long)raw * 1000000) >> SM5705_FG_FRACTIONAL_BITS;
}

static int sm5705_fg_get_current_ua(struct sm5705_fg *fg, int *out)
{
	int raw = sm5705_fg_read(fg, SM5705_FG_CURRENT);

	if (raw < 0)
		return raw;

	/* Signed: negative while the battery is being drained. */
	*out = sm5705_fg_to_micro((s16)raw);

	return 0;
}

static int sm5705_fg_get_status(struct sm5705_fg *fg, int *out)
{
	int ret, cur, soc;

	ret = sm5705_fg_get_current_ua(fg, &cur);
	if (ret)
		return ret;

	if (cur > SM5705_FG_CURRENT_NOISE_UA) {
		*out = POWER_SUPPLY_STATUS_CHARGING;
		return 0;
	}

	if (cur < -SM5705_FG_CURRENT_NOISE_UA) {
		*out = POWER_SUPPLY_STATUS_DISCHARGING;
		return 0;
	}

	/*
	 * Sitting still. Full if the gauge says so, otherwise there is external
	 * power holding it steady without charging.
	 */
	soc = sm5705_fg_read(fg, SM5705_FG_SOC);
	if (soc < 0)
		return soc;

	*out = (soc >> 8) >= 99 ? POWER_SUPPLY_STATUS_FULL :
				  POWER_SUPPLY_STATUS_NOT_CHARGING;

	return 0;
}

/*
 * Notice a plug or unplug and tell the power-supply core about it.
 *
 * Deliberately fires power_supply_changed() ONLY on a transition. Calling it
 * every poll would wake the whole desktop stack twice a second for nothing;
 * calling it never is what made the tablet sit there claiming to be charging
 * long after the cable came out.
 */
static void sm5705_fg_poll(struct work_struct *work)
{
	struct sm5705_fg *fg = container_of(work, struct sm5705_fg,
					    poll_work.work);
	int status;

	if (!sm5705_fg_get_status(fg, &status) && status != fg->last_status) {
		dev_dbg(&fg->client->dev, "status %d -> %d\n",
			fg->last_status, status);
		fg->last_status = status;
		power_supply_changed(fg->psy);
	}

	schedule_delayed_work(&fg->poll_work, SM5705_FG_POLL_INTERVAL);
}

static int sm5705_fg_get_property(struct power_supply *psy,
				  enum power_supply_property psp,
				  union power_supply_propval *val)
{
	struct sm5705_fg *fg = power_supply_get_drvdata(psy);
	int ret, raw;

	switch (psp) {
	case POWER_SUPPLY_PROP_STATUS:
		return sm5705_fg_get_status(fg, &val->intval);

	case POWER_SUPPLY_PROP_VOLTAGE_NOW:
		raw = sm5705_fg_read(fg, SM5705_FG_VBAT);
		if (raw < 0)
			return raw;
		val->intval = sm5705_fg_to_micro(raw);
		return 0;

	case POWER_SUPPLY_PROP_VOLTAGE_OCV:
		raw = sm5705_fg_read(fg, SM5705_FG_OCV);
		if (raw < 0)
			return raw;
		val->intval = sm5705_fg_to_micro(raw);
		return 0;

	case POWER_SUPPLY_PROP_CURRENT_NOW:
		return sm5705_fg_get_current_ua(fg, &val->intval);

	case POWER_SUPPLY_PROP_CAPACITY:
		raw = sm5705_fg_read(fg, SM5705_FG_SOC);
		if (raw < 0)
			return raw;
		/* High byte is whole percent; clamp, the part can read over. */
		val->intval = min(raw >> 8, 100);
		return 0;

	case POWER_SUPPLY_PROP_PRESENT:
		ret = sm5705_fg_read(fg, SM5705_FG_DEVICE_ID);
		val->intval = ret >= 0;
		return 0;

	case POWER_SUPPLY_PROP_TECHNOLOGY:
		val->intval = POWER_SUPPLY_TECHNOLOGY_LION;
		return 0;

	case POWER_SUPPLY_PROP_CHARGE_FULL_DESIGN:
		if (!fg->info || fg->info->charge_full_design_uah < 0)
			return -ENODATA;
		val->intval = fg->info->charge_full_design_uah;
		return 0;

	case POWER_SUPPLY_PROP_VOLTAGE_MIN_DESIGN:
		if (!fg->info || fg->info->voltage_min_design_uv < 0)
			return -ENODATA;
		val->intval = fg->info->voltage_min_design_uv;
		return 0;

	case POWER_SUPPLY_PROP_VOLTAGE_MAX_DESIGN:
		if (!fg->info || fg->info->voltage_max_design_uv < 0)
			return -ENODATA;
		val->intval = fg->info->voltage_max_design_uv;
		return 0;

	default:
		return -EINVAL;
	}
}

static enum power_supply_property sm5705_fg_props[] = {
	POWER_SUPPLY_PROP_STATUS,
	POWER_SUPPLY_PROP_PRESENT,
	POWER_SUPPLY_PROP_TECHNOLOGY,
	POWER_SUPPLY_PROP_CAPACITY,
	POWER_SUPPLY_PROP_VOLTAGE_NOW,
	POWER_SUPPLY_PROP_VOLTAGE_OCV,
	POWER_SUPPLY_PROP_CURRENT_NOW,
	POWER_SUPPLY_PROP_CHARGE_FULL_DESIGN,
	POWER_SUPPLY_PROP_VOLTAGE_MIN_DESIGN,
	POWER_SUPPLY_PROP_VOLTAGE_MAX_DESIGN,
};

static const struct power_supply_desc sm5705_fg_desc = {
	.name		= "sm5705-fuelgauge",
	.type		= POWER_SUPPLY_TYPE_BATTERY,
	.properties	= sm5705_fg_props,
	.num_properties	= ARRAY_SIZE(sm5705_fg_props),
	.get_property	= sm5705_fg_get_property,
};

static int sm5705_fg_probe(struct i2c_client *client)
{
	struct power_supply_config cfg = {};
	struct device *dev = &client->dev;
	struct sm5705_fg *fg;
	int ret;

	if (!i2c_check_functionality(client->adapter,
				     I2C_FUNC_SMBUS_READ_WORD_DATA))
		return dev_err_probe(dev, -ENODEV,
				     "adapter cannot do SMBus word reads\n");

	fg = devm_kzalloc(dev, sizeof(*fg), GFP_KERNEL);
	if (!fg)
		return -ENOMEM;

	fg->client = client;

	/* Prove the part is really there before claiming a battery exists. */
	ret = i2c_smbus_read_word_data(client, SM5705_FG_DEVICE_ID);
	if (ret < 0)
		return dev_err_probe(dev, ret, "no response from fuel gauge\n");

	dev_info(dev, "SM5705 fuel gauge, device id 0x%04x\n", ret);

	cfg.drv_data = fg;
	cfg.fwnode = dev_fwnode(dev);

	fg->psy = devm_power_supply_register(dev, &sm5705_fg_desc, &cfg);
	if (IS_ERR(fg->psy))
		return dev_err_probe(dev, PTR_ERR(fg->psy),
				     "failed to register power supply\n");

	/*
	 * Optional. Without it the design capacity and voltage limits are
	 * simply not offered; everything measured still works.
	 */
	ret = power_supply_get_battery_info(fg->psy, &fg->info);
	if (ret)
		dev_warn(dev, "no battery info: %d\n", ret);

	/* Seed the state so the first poll does not report a false transition. */
	if (sm5705_fg_get_status(fg, &fg->last_status))
		fg->last_status = POWER_SUPPLY_STATUS_UNKNOWN;

	ret = devm_delayed_work_autocancel(dev, &fg->poll_work, sm5705_fg_poll);
	if (ret)
		return ret;

	schedule_delayed_work(&fg->poll_work, SM5705_FG_POLL_INTERVAL);

	return 0;
}

static const struct of_device_id sm5705_fg_of_match[] = {
	{ .compatible = "siliconmitus,sm5705-fuelgauge" },
	{ }
};
MODULE_DEVICE_TABLE(of, sm5705_fg_of_match);

static const struct i2c_device_id sm5705_fg_id[] = {
	{ "sm5705-fuelgauge" },
	{ }
};
MODULE_DEVICE_TABLE(i2c, sm5705_fg_id);

static struct i2c_driver sm5705_fg_driver = {
	.driver = {
		.name = "sm5705-fuelgauge",
		.of_match_table = sm5705_fg_of_match,
	},
	.probe = sm5705_fg_probe,
	.id_table = sm5705_fg_id,
};
module_i2c_driver(sm5705_fg_driver);

MODULE_DESCRIPTION("Silicon Mitus SM5705 fuel gauge");
MODULE_LICENSE("GPL");
