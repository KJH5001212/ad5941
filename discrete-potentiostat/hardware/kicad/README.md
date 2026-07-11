# KiCad 회로도

`discrete-potentiostat.kicad_sch` — KiCad 7 포맷 (KiCad 7/8/9에서 열림), 심볼 전부 파일에 내장(라이브러리 설치 불필요).
`discrete-potentiostat.pdf` — 렌더링본.

## 검증 상태 (Rev A)

| 항목 | 상태 |
|---|---|
| 넷 연결 | ✅ **설계 넷리스트 38개 vs `kicad-cli` 추출 넷리스트 완전 일치** (`generator/check_nets.py`) |
| BQ51013BRHL 핀맵 | ✅ KiCad 공식 BQ51050BRHL 심볼(동일 계열 패키지) 기반 + 차이 핀(4 OUT, 10 EN1) 데이터시트 확인 |
| MCP73832 핀맵 | ✅ KiCad 공식 라이브러리 (1 STAT / 2 VSS / 3 VBAT / 4 VDD / 5 PROG) |
| MIC5205 핀맵 | ✅ KiCad 공식 라이브러리 (1 IN / 2 GND / 3 EN / 4 BYP / 5 OUT) |
| OPA2391 DGK 핀맵 | ✅ 데이터시트 확인 (표준 듀얼: 1 OUTA / 2 −INA / 3 +INA / 4 V− / 5 +INB / 6 −INB / 7 OUTB / 8 V+) |
| REF35102 DBV 핀맵 | ✅ 데이터시트 확인 (1,2 GND / 3 EN / 4 VIN / 5 NR / 6 VREF) |
| nRF52832-QFAA 핀맵 (48+EP) | ✅ KiCad 공식 라이브러리 (MCU_Nordic:nRF52832-QFxx) |
| RF 프런트엔드 | ✅ Johanson 2450FM07A0029 = nRF52832 QFN 전용 매칭 LPF (벤더 확인) — 4핀 배치는 데이터시트 랜드패턴 따를 것 |
| DEC2/DEC3 미연결 | ✅ QFN48 레퍼런스 기준 (DEC2 확인됨) / ⚠️ DEC3는 최신 PS로 재확인 권장 |
| 충전전류 공식 | ✅ MCP73832: ICHG = 1000/RPROG = 20 mA · BQ51013B ILIM: RILIM = 250/IMAX |

**칩다운 전환(2026-07-10)**: MDBT42Q 모듈 → nRF52832-QFAA 베어칩. RF 체인은
ANT(30) → FL1(매칭 LPF) → R15(0R)/C34(DNP) 튜닝 슬롯 → 칩안테나. RF 레이아웃은 Nordic
QFN48 레퍼런스를 트레이스 형상까지 복사하고, 실보드에서 VNA로 안테나 튜닝할 것.

## 핀 할당 (nRF52832)

| 기능 | 핀 | QFN48 핀 번호 |
|---|---|---|
| SAADC 차동 N (VREF 1.024 V) | P0.02/AIN0 | 4 |
| SAADC 차동 P (TIA 출력) | P0.03/AIN1 | 5 |
| VBAT 모니터 | P0.04/AIN2 | 6 |
| AFE 전원 (high-drive) | P0.06 | 8 |
| 상태 LED | P0.07 | 9 |
| 충전 상태 (STAT, 100k 풀업) | P0.08 | 10 |
| RESET / SWD | P0.21(24) / SWDCLK(25) / SWDIO(26) | — |

## PCB (`discrete-potentiostat.kicad_pcb`)

**24×22 mm, 6층** (사용자 결정 2026-07-11: 4층 대비 자동배선 미결선 20링크→8링크, RF/아날로그 격리 개선).
스택업: **L1 신호/RF · L2 GND · L3 배선 · L4 배선 · L5 GND · L6 신호+배터리 패드**.
설계규칙(보드에 저장): 트랙 최소 0.09 / 클리어런스 0.09 / 비아 0.4/0.2 — JLC 6층 표준 공정 내.

| 항목 | 상태 |
|---|---|
| 배치 | ✅ 겹침 0 · 보드이탈 0 (`check_pcb.py`) — J1/J2는 **Ø1.0 mm 원형 납땜 패드**(와이어 직납, 피치 1.8 mm)로 변경 |
| GND | ✅ L2·L5 통짜 플레인 + F/B 포어 + 스티칭 비아 |
| RF 급전 | ✅ ANT→FL1→pi 슬롯→안테나 선행 배선 (보호됨) |
| 신호 배선 | 🟨 자동배선 ~92% — **미결선 15패드(≈8링크)**: XC1(Y1·C32·U3.34), C4 양단(BOOT2/AC2), RECT(U1.18·C9), ADC_P(U3.5·C23), ADC_N(C24·R13), CLAMP1(C7), COMP(C21), VREF(TP1) |
| DRC | 🟨 `drc-report.txt`: 미결선 30에지 + 클리어런스 7 + 댕글링 11 (실크/코트야드 경고 별도) |

### 마무리 가이드 (KiCad GUI, ~30분)

1. 보드 열기 → `B`(존 채우기) → 흰 래츠네스트 = 남은 8링크
2. `X` 라우팅 — **푸시&쇼브가 켜져 있으면 기존 트랙을 밀어내며 그려짐** (자동 라우터가 못 뚫은 지점들은 전부 이 방식으로 뚫림). L3/L4 내층이 비어 있으니 비아 2개로 우회 가능
3. Edit → Cleanup Tracks & Vias (댕글링 11 제거) → DRC 재실행 → 클리어런스 7건 정리
4. 커스텀 풋프린트 3종(FL1/E1/코일 패드) 랜드패턴 데이터시트 대조 후 가버 출력

## 재생성 방법

```bash
sudo apt install kicad kicad-symbols   # 공식 심볼 라이브러리 필요
cd generator
python3 extract_syms.py                # 시스템 라이브러리에서 베이스 심볼 추출
python3 gen_sch.py                     # ../discrete-potentiostat.kicad_sch 생성
kicad-cli sch export netlist --output out.net ../discrete-potentiostat.kicad_sch
python3 check_nets.py                  # 넷리스트 일치 확인 (RESULT: PASS 확인)
python3 gen_pcb.py                     # ../discrete-potentiostat.kicad_pcb 생성 (pcbnew API)
python3 check_pcb.py                   # 배치 겹침/보드이탈 검사 (flagged: 0 확인)
python3 route_pcb.py                   # 자동배선 (GND 스티칭 + A* 립업 라우터, 6층)
RESUME=1 python3 route_pcb.py          # 기존 배선 보존, 미결선만 이어서 배선
# todo_override.json(DRC 미결선 목록)을 두면 RESUME이 그 목록만 공략
```

### 배선 마무리 가이드 (KiCad GUI)

1. 보드 열고 `B`(존 채우기) → 흰 래츠네스트가 남은 미결선 (~20개, 대부분 U1 충전부 캡열과 U3 남서쪽)
2. `X`로 라우팅 — 규칙은 이미 0.1/0.1로 설정됨, B면이 여유 있으니 비아 활용
3. DRC 재실행해 클리어런스 12건(그리드 라우터의 45° 코너 근접) 정리
4. track_dangling 11건 = 립업 잔재 스텁 — Edit > Cleanup Tracks & Vias로 일괄 제거

배치를 바꾸려면 `gen_sch.py`의 `layout()` 좌표를 수정하거나, KiCad GUI에서 직접 옮기면 된다
(연결은 라벨 기반이라 심볼을 옮겨도 넷은 유지됨).

## 회로도에 박혀 있는 주의사항 (DESIGN NOTES 텍스트 블록)

1. 충전은 2단: BQ51013B(Qi→5V, ILIM 100 mA) + MCP73832(20 mA/4.2 V, 종지 1.5 mA) — BQ51050B 직접충전(TI <200 mA 비권장) 대체
2. 공진탱크는 L=47 µH 기준 — 코일 변경 시 C1/C2 재계산
3. 칩다운 RF: Nordic QFN48 레퍼런스 레이아웃 복사 + 실보드 안테나 튜닝(R15/C34 슬롯), DEC3 최신 PS 확인
4. WE 노드 가드링(VREF 구동), 플럭스 세정
5. R10/C21(보상망)은 DNP — CE-RE 루프 발진 시만 실장
6. LIR2032 무보호 — 3.0 V 미만 System OFF 펌웨어 필수
