# 디스크리트 포텐쇼스탯 펌웨어 (nRF52832 SAADC 직접측정)

WE-RE **+0.512 V 하드웨어 고정 바이어스**(REF35102 + 1:1 분압)를 걸고,
LPV802 TIA 출력을 **nRF52832 SAADC 가 직접 측정**하는 크로노암페로메트리 펌웨어.
AD5941 버전(`/firmware`)과 **BLE 프로토콜 동일** — 기존 안드로이드 앱이 무수정으로 동작하고,
측정 파라미터(rf/os/게인 등)를 앱에서 조절하는 확장 필드가 추가됐다.

## AD5941 버전과의 차이

| | AD5941 버전 (`/firmware`) | 이 펌웨어 |
|---|---|---|
| 측정 | AD5941 이 측정, nRF 는 SPI 수신 | **nRF SAADC 가 직접 측정** (SPI 없음) |
| 전위 | LPDAC 로 생성 (+0.5 V) | 하드웨어 고정 (+0.512 V, REF35102) |
| 레인지 | RTIA 저항 전환 (11단) | **SAADC 게인 전환 (3단)** — RF 는 고정 저항 |
| RF | 내부 RTIA | 외부 1 MΩ (5.1 M/10 M 교체 가능) → **`rf` 런타임 설정** |
| 분해능 | RTIA 오토레인지 | HW 오버샘플링(`os`) + 게인 4 구간, ≤0.1 nA |
| 캘리브레이션 | 없음 | **zero(오프셋)/cal(스케일)** — NVM 저장 |
| 배터리 | 없음 | VBAT 감시, 3.3 V 경고 / 3.0 V System OFF |

## 구성

| 파일 | 역할 |
|------|------|
| `src/main.c` | 측정 스레드(페이싱·오토레인지·캘리브레이션) · BLE NUS · 배터리 정책 |
| `src/meas.c` | SAADC 엔진 (차동 AIN0−AIN1, 게인 3단, HW 오버샘플) + AFE 전원 + VBAT/충전/Qi |
| `src/store.c` | `rf`/`os`/캘리브레이션 NVM 영속화 (settings/NVS) |
| `src/databuf.c` | 무손실 링버퍼 + ACK 윈도우 (AD5941 버전 그대로) |
| `src/cmd.c` | JSON 명령 파서 (rf/os/zero/cal 확장) |
| `app.overlay` | 보드 핀맵 · SAADC 활성 · LDO 모드(DCC 미접속 보드 기준) |
| `prj.conf` | BLE(NUS)/ADC/FPU/RTT/settings/LFRC 설정 |

## 측정 원리

- **바이어스**: 하드웨어 고정. AVDD 급전 GPIO(P0.26) HIGH → REF35102(1.024 V) + LPV802 급전(~2 µA),
  50 ms 안정화 후 샘플링. 정지 시 LOW → 소모 0 + CE 차단(전극 분극 방지).
  (회로도 요건: DVDD→AVDD 상시 연결(R7 등) 제거, AVDD 는 P0.26 이 단독 급전)
- **TIA**: `V_TIA = 1.024 V + I×RF`. SAADC **차동**(AIN0=TIA_OUT, AIN1=1.024 V)이라
  ΔV = I×RF 를 오프셋 없이 직접 획득.
- **게인 레인지** (RF=1 MΩ 기준): idx0 = gain4 (±150 nA, LSB 73 pA) /
  idx1 = gain1 (±0.6 µA) / idx2 = gain1/2 (−0.9~+2.1 µA).
  오토레인지: |ΔV| > 85 % FS → 게인 다운, < 18 % FS → 게인 업 (히스테리시스+settle 폐기).
- **분해능**: HW 오버샘플 `os`(기본 64×) + 출력주기 내 소프트 평균 → ≤0.1 nA (요구 1 nA 대비 10×).
- **전류 환산**: `I[nA] = (ΔV/rf_ohm)×1e9 × scale − off[gain]` — scale/off 는 캘리브레이션값(NVM).

## BLE 프로토콜 (NUS, JSON 라인)

### 명령 (앱 → 기기) — 기존 앱 호환

```json
{"cmd":"start","mode":"continuous|timed|cycle","rate":10,"dur":60,"on":5,"off":295,"cycles":0,"auto":1,"range":0}
{"cmd":"config", ...같은 필드... }        // 실행 없이 설정만
{"cmd":"stop"}  {"cmd":"status"}  {"cmd":"ack","seq":1234}
```

### 확장: 측정 파라미터 (config/start 에 추가 필드)

| 필드 | 의미 | 범위 | 비고 |
|---|---|---|---|
| `rf` | TIA RF [Ω] | 10 k~100 M | 보드에서 RF 교체 시 전송 → **NVM 저장** (리플래시 불필요) |
| `os` | HW 오버샘플 | 1~256 | 2^n 절사, NVM 저장. RF 클수록 낮춰도 됨 (1 M→64, 10 M→4) |
| `auto` | 오토레인지 | 0/1 | 0 이면 `range` 게인 고정 |
| `range` | 게인 인덱스 | 0~2 | 0=최고감도(±150 nA@1 M) … 2=풀레인지(±2.1 µA@1 M) |

### 확장: 캘리브레이션 (유휴 상태에서만)

```json
{"cmd":"zero"}              // 셀 오픈(전극 미연결) 상태에서 — 게인별 오프셋 측정+저장
{"cmd":"cal","kohm":1000}   // 기지저항 더미셀 연결 상태 — 기대 I=512mV/R 로 스케일 보정
```
응답: `{"cal":"zero","off":[...]}` / `{"cal":"scale","exp_nA":...,"meas_nA":...,"scale":...}`

**RF 교체 절차**: ① 새 RF/CF 납땜 ② `{"cmd":"config","rf":5100000,"os":16}` ③ `zero` + `cal`.

### 데이터 (기기 → 앱) — 기존 앱 호환

```json
{"d":[[seq,t_ms,current_nA,range], ...]}   // 무손실 스트림 (ack 로 해제)
{"st":"idle|run|rest","mode":0,"rate":10,"cyc":3,"range":1,"pend":0,"buf":12,"gap":0,
 "vbat":3720,"chg":1,"qi":1,"lb":0,"rf":1000000,"os":64}   // 상태 (1초 주기) — vbat 이후가 확장 필드
```

`chg` = 실제 충전 중(MCP73832 STAT) · `qi` = 충전패드 위(Qi 5V 수신, AIN3 분압 >2 V).
둘을 조합하면 앱에서 3상태 구분: 패드 밖(`qi:0`) / 충전 중(`qi:1,chg:1`) / 완충(`qi:1,chg:0`).

## 배터리 정책 (LIR2032 무보호 셀)

- 5초마다 VBAT(AIN2, 1M:1M 분압) 측정 → status 의 `vbat`[mV].
- **< 3.3 V**: `lb:1` 경고, 신규 start 거부 (실행중 측정은 계속).
- **< 3.0 V** (충전중 아닐 때): 측정 정지 → 버퍼 플러시(최대 10 s) → **System OFF**.
- `chg`: MCP73832 STAT (1=충전중). 충전 중 측정은 노이즈 증가 — 앱에서 경고 권장.

## 빌드 (NCS, AD5941 버전과 동일 환경)

```powershell
west build -b nrf52dk/nrf52832 <이 폴더> -d <이 폴더>\build --pristine
```

- 보드 타깃은 DK 지만 `app.overlay` 가 실보드 핀맵/전원으로 전부 재정의한다.
- 32.768 kHz 크리스탈 없음 → `CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y` (이미 설정).
- DCC 미접속(인덕터 없음) → LDO 모드 (nRF 기본값 — overlay 에 `&reg` 오버라이드 없음).
  DC/DC 인덕터를 실장한 보드라면 `&reg { regulator-initial-mode = <NRF5X_REG_MODE_DCDC>; }` 추가.
- 플래시: J-Link + SWD (SWDIO/SWDCLK 테스트포인트 + 3V3/GND).

### 핀맵 (app.overlay 와 일치 — 다른 보드 리비전이면 overlay 만 수정)

| 기능 | 핀 | 비고 |
|---|---|---|
| TIA 출력 (차동 +) | P0.02/AIN0 | ΔV = I×RF |
| VREF 1.024 V (차동 −) | P0.03/AIN1 | REF35102 |
| VBAT 모니터 | P0.04/AIN2 | 1M:1M 분압 |
| Qi 패드 감지 (`qi`) | P0.05/AIN3 | MCP73832_VDD(≈5 V) 1M:1M 분압 |
| AVDD 급전 (AFE 전원) | P0.26 | high-drive 출력 — DVDD→AVDD 상시 연결 없어야 함 |
| 충전 상태 (`chg`) | P0.08 | MCP73832 STAT, 100k 풀업, LOW=충전중 |
| LED (옵션) | P0.06 | 미실장이어도 무해 |

## 검증 순서 (firmware-plan.md §검증)

1. RTT 로 부팅 확인 → `status` 로 vbat/rf/os 확인
2. 더미셀(1 MΩ) 연결 → `start` → 이론값 512 nA 부근 확인 → `cal` 로 보정
3. 셀 오픈 → `zero` → 오프셋(±3.5 nA@1 M, LPV802 Vos) 제거 확인
4. 기지저항 3점(100 k/1 M/10 M)으로 게인별 선형성·오토레인지 전환 확인
5. 충전 중 노이즈 플로어 측정 → "충전 중 측정 금지" 정책 확정
