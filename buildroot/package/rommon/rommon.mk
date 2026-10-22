ROMMON_VERSION = 0.5
ROMMON_SITE = $(BR2_EXTERNAL_FRACTALCR_PATH)/../rommon
ROMMON_SITE_METHOD = local
ROMMON_LICENSE = MIT
ROMMON_DEPENDENCIES = openssl

define ROMMON_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) -Os -Wall -Wextra \
		-I$(BR2_EXTERNAL_FRACTALCR_PATH)/../../build/keys \
		-I$(@D)/service $(@D)/rommon.c $(@D)/monitor.c \
		$(@D)/service/supervisor.c $(@D)/service/control.c $(@D)/service/logging.c \
		-o $(@D)/rommon $(TARGET_LDFLAGS) -lcrypto
	$(TARGET_CC) $(TARGET_CFLAGS) -Os -Wall -Wextra -Werror -DFCR_ROMMON \
		-I$(@D)/service $(@D)/service/manager.c $(@D)/service/graph.c \
		$(@D)/service/supervisor.c -o $(@D)/supervisor $(TARGET_LDFLAGS)
endef

define ROMMON_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/supervisor $(TARGET_DIR)/init
	$(INSTALL) -D -m 0755 $(@D)/rommon $(TARGET_DIR)/usr/libexec/fractal-recovery
endef

$(eval $(generic-package))
