#
# AD5941 3-electrode potentiostat firmware (nRF52832, NCS v3.4)
# 3전극 정전위 크로노암페로메트리 브링업 펌웨어
#
cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(ad5941_pstat)

# ADI AD5940 라이브러리는 대상 칩을 컴파일 타임에 선택해야 함.
# AD5941 = AD594x 계열 -> CHIPSEL_594X. (헤더 상단의 #error 방지)
target_compile_definitions(app PRIVATE CHIPSEL_594X)

target_sources(app PRIVATE
	src/main.c
	src/databuf.c              # 무손실 데이터 버퍼
	src/cmd.c                  # JSON 명령 파서
	src/ad5940_port.c          # nRF52832 / Zephyr HAL 포트 계층
	src/ad5940lib/ad5940.c     # ADI 공식 라이브러리 (벤더링, 수정 금지)
)

target_include_directories(app PRIVATE src/ad5940lib)
