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
| LPV802 DGK 핀맵 | ✅ 표준 듀얼 (1 OUTA / 2 −INA / 3 +INA / 4 V− / 5 +INB / 6 −INB / 7 OUTB / 8 V+) — VSSOP-8, OPA2391과 핀·풋프린트 호환 |
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

**26×24 mm, 6층, 양면 실장** (2026-07-11 확정 — 사용자 승인: "크기를 약간 늘리더라도 최대한 밀집 배치").
IC는 전부 윗면, 수동소자 11개(R1·R2·TH1·R3~R6·C15·R10·C21·J2)는 뒷면 가장자리 링에 배치
— 뒷면 중앙 **Ø21 mm 원**(Cmts 레이어 표시)은 LIR2032 탭셀 전용 구역으로 비워둠.
스택업: **L1 신호/RF · L2 GND · L3 배선 · L4 배선 · L5 GND · L6 신호+배터리 패드**.
설계규칙(보드에 저장): 트랙 최소 0.09 / 클리어런스 0.09 / 비아 0.4/0.2 — JLC 6층 표준 공정 내.

| 항목 | 상태 |
|---|---|
| 배치 | ✅ 겹침 0 · 보드이탈 0 (`check_pcb.py`) — J1/J2는 **Ø1.0 mm 원형 납땜 패드**(와이어 직납, 피치 1.8 mm). C1/C2 몸체 간격 ~0(패드 간격 0.105 mm — 표준 실장 한계 내, 조립 시 확인) |
| GND | ✅ L2·L5 통짜 플레인 + F/B 포어 + 스티칭 비아, 고립 동박 0 (0.7 mm² 미만 섬 자동 제거) |
| RF 급전 | ✅ ANT(U3.30)→FL1→pi 슬롯(C35/R15/C34)→칩안테나 — 클리어런스 검증된 경로로 재배선 완료 |
| 신호 배선 | ✅ **전 넷 100% 자동배선 완료** (77넷, 미결선 0) |
| DRC | ✅ `drc-report.txt`: **동박 위반 0** (미결선 0 · 클리어런스 0 · 엣지 0). 남은 항목은 실크 겹침 124/코트야드 40/스타브드 서멀 9 등 경고성 — 밀집 배치·인라인 풋프린트에서 나오는 코스메틱 |

### 남은 작업 (KiCad GUI에서 배선 불필요!)

1. 보드 열기 → `B`(존 채우기) → DRC 실행해 동박 위반 0 확인
2. 커스텀 풋프린트 3종(FL1/E1/코일 패드) 랜드패턴을 데이터시트와 대조
3. 실크 정리(선택) → File → Fabrication Outputs → Gerber 출력
4. 안테나는 실보드에서 VNA로 튜닝 (R15 0R ↔ C34/C35 DNP 슬롯)

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
./route_loop.sh                        # DRC→미결선 목록→RESUME 수렴 루프 (×4)
# todo_override.json(DRC 미결선 목록)을 두면 RESUME이 그 목록만 공략
# PRIORITY=NET1,NET2 환경변수로 특정 넷을 최우선 배선 (막힌 코너 공략용)
```

배선이 특정 코너에서 계속 실패하면: 그 통로를 점거한 넷들의 트랙을 지우고
todo_override.json에 [실패넷들 우선, 걷어낸 넷들 나중] 순서로 넣은 뒤
`PRIORITY=... RESUME=1`로 재실행 (U1 QFN 코너가 이 방법으로 뚫렸음).
`gnd_fix.py`(GND 고아 패드/고립 섬 스티칭)와 `copper_fix.py`(RF 경로 검증 재배선,
좌표 하드코딩된 1회성 스크립트)는 기록용으로 남겨둠.

배치를 바꾸려면 `gen_pcb.py`의 `PLACE` 좌표를 수정하거나, KiCad GUI에서 직접 옮기면 된다
(회로 연결은 라벨 기반이라 심볼을 옮겨도 넷은 유지됨).

## 회로도에 박혀 있는 주의사항 (DESIGN NOTES 텍스트 블록)

1. 충전은 2단: BQ51013B(Qi→5V, ILIM 100 mA) + MCP73832(20 mA/4.2 V, 종지 1.5 mA) — BQ51050B 직접충전(TI <200 mA 비권장) 대체
2. 공진탱크는 L=47 µH 기준 — 코일 변경 시 C1/C2 재계산
3. 칩다운 RF: Nordic QFN48 레퍼런스 레이아웃 복사 + 실보드 안테나 튜닝(R15/C34 슬롯), DEC3 최신 PS 확인
4. WE 노드 가드링(VREF 구동), 플럭스 세정
5. R10/C21(보상망)은 DNP — CE-RE 루프 발진 시만 실장
6. LIR2032 무보호 — 3.0 V 미만 System OFF 펌웨어 필수
