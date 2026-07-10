# AD5941 3전극 정전위 펌웨어 (nRF52832)

WE-RE 사이 **+0.5V 고정 바이어스**를 걸고 **나노암페어급 전류**를 읽는
크로노암페로메트리(chronoamperometry) 펌웨어. 측정값을 **BLE NUS 로 스트리밍** + SEGGER RTT 로그.

## 구성

| 파일 | 역할 |
|------|------|
| `src/main.c` | AFE 구성 · 측정 스레드(전류 평균) · BLE NUS 전송 · RTT 로그 |
| `src/ad5940_port.c` | ADI AD5940 라이브러리 ↔ nRF52832/Zephyr HAL 포트 (SPI/GPIO/INT) |
| `src/ad5940lib/` | ADI 공식 라이브러리 (벤더링, **수정 금지**) |
| `app.overlay` | 핀 배선 · LDO 모드 |
| `prj.conf` | SPI/GPIO/FPU/RTT/**BLE(NUS)** 설정 |

BLE 구조는 `emg_ble` 의 NUS 를 재활용 — 광고 재시작 워크큐, 우리 쪽 MTU 교환,
notify(CCCD) 게이팅, MTU 청크 전송. 측정은 별도 스레드가 최신 전류를 갱신하고
main 은 그 값을 주기 전송한다.

## 측정 원리

- **LPDAC**: Vzero(6bit)=1.1V 로 WE 동작점, Vbias(12bit)=0.6V 로 RE 구동 → V(WE)-V(RE)=+0.5V
- **LPTIA**: WE 전류를 내부 RTIA(512kΩ)로 전압 변환. **I = Vadc/RTIA** (TIA 측정값 그대로, 부호 조작 없음)
- **ADC**: 800kSPS → SINC3(OSR4) → SINC2(OSR1333) ≈ 150SPS, 6샘플 평균 → **~25Hz 출력**
- 전류 범위 1~100nA 기준: 100nA→51mV, 1nA→512µV (매우 저전류면 외부 RTIA 고려)

## 빌드

```powershell
$TC="C:\ncs\toolchains\dcbdc366a1"
$env:PATH="$TC\opt\bin;$TC\opt\bin\Scripts;$TC\mingw64\bin;$TC\bin;$TC\usr\bin;C:\Users\KIM\Downloads;$env:PATH"
$env:ZEPHYR_BASE="C:\ncs\v3.4.0\zephyr"
$env:ZEPHYR_SDK_INSTALL_DIR="$TC\opt\zephyr-sdk"
$env:ZEPHYR_TOOLCHAIN_VARIANT="zephyr"
west build -b nrf52dk/nrf52832 C:\Users\KIM\Downloads\ad5941_pstat -d C:\Users\KIM\Downloads\ad5941_pstat\build --pristine
```

## 플래시 (DK PCA10040 프로그래머 경유)

```
nrfutil device program --serial-number 1050366520 --firmware build\ad5941_pstat\zephyr\zephyr.hex
nrfutil device reset --serial-number 1050366520
```
> 국룰: `recover → program → reset` 한 호흡. 중간에 전원/리셋 금지 (APPROTECT 자동잠금 방지).

## 로그/데이터 보기

- **RTT**: SEGGER RTT Viewer 연결 → `I = xx.xxx nA` 스트림 (폰 없이 확인).
- **BLE**: 폰의 nRF Connect 앱에서 **`ad5941`** 연결 → NUS TX(6E400003) notify 켜면
  `123.456 nA  0.0512 mV` 형식으로 25Hz 스트리밍. 1초마다 `STATUS afe=OK cnt=...` 헤더.
  - notify 를 켜도 데이터가 없으면 RTT 의 `NUS notify ENABLED` / `bt_nus_send 실패` 로그로 진단.

## ⚠ 벤치에서 반드시 확인할 것

1. **핀 배선** — `app.overlay` 의 SCLK/MOSI/MISO/CS/RESET/INT 는 **임시 기본값**.
   실제 스키매틱에 맞춰 수정 (`TODO` 표시).
2. **ADIID** — 부팅 로그에 `ADIID = 0x4144` 나와야 SPI/전원 정상.
3. **LPTIA 스위치** — `LPTIASW(2)|LPTIASW(4)|LPTIASW(5)` = 외부 3전극 표준.
   신호 없으면 AD5940 데이터시트 LP loop 스위치 그림과 대조.
4. **전극 핀** — AD5941 CE0=상대, RE0=기준, SE0=작업(WE) 전극.

## 파라미터 조정 (`src/main.c` 상단)

- `CELL_BIAS_MV` : 셀 바이어스(mV)
- `RTIA_SEL` / `RTIA_OHM` : TIA 이득저항 (외부 시 `LPTIARTIA_OPEN`)
- `ADC_PGA` : 저전류일수록 크게
- `AVG_SAMPLES` : 평균 개수(출력 레이트 = 150/N Hz)

## 다음 단계

- 실측 검증(ADIID·전류값) 후 RTIA/PGA 튜닝
- BLE 안정성 이슈 시: 32k 크리스탈 실장 + XTAL 전환(prj.conf) / `HFXO_ALWAYS_ON=1`(main.c)
- INT 핀(P0.13, AD5941 GPIO0) 기반 인터럽트/시퀀서 + FIFO 저전력 모드로 전환 (현재는 폴링)
- Unity AR 앱(EMG_AR) BLE 수신 연동
