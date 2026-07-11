#
# Discrete potentiostat firmware (nRF52832 SAADC, NCS)
# 3전극 정전위 크로노암페로메트리 — AD5941 없이 SAADC 직접측정
#
cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(discrete_pstat)

target_sources(app PRIVATE
	src/main.c
	src/meas.c                 # SAADC 측정 엔진 + AFE 전원 + 배터리
	src/store.c                # rf/os/cal NVM 영속화 (settings)
	src/databuf.c              # 무손실 데이터 버퍼 (AD5941 버전 재사용)
	src/cmd.c                  # JSON 명령 파서 (rf/os/zero/cal 확장)
)
