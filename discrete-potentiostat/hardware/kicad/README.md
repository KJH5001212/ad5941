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

## PCB (`discrete-potentiostat.kicad_pcb`) — RevC

**23.4×22 mm, 6층, 양면 실장** (2026-07-11 RevC — RevB 26×24 대비 면적 **-17.5%**).
크기 하한 근거: 폭 = 배터리 탭 패드(중심 ±9.8 + 패드 1.4 + 엣지 0.3 ≈ 23.0),
높이 = 셀 존 Ø21 + 여유. **이보다 더 줄이려면 배터리/탭 구조 자체를 바꿔야 함.**
IC 전부 윗면; 뒷면은 코너 포켓 4곳(R1·R2·TH1 좌상 / R3 우하 / R5·R6·C15 좌하)만 사용
— 뒷면 중앙 **Ø21 mm 원**(Cmts 표시)은 LIR2032 탭셀 전용. J2(SWD)·TP1-3은 앞면 우측 열로
이동(배터리와 충돌 방지 + 납땜 접근성), R4·R10/C21(DNP)은 앞면 여유 공간.
스택업: **L1 신호/RF · L2 GND · L3 배선 · L4 배선 · L5 GND · L6 신호+배터리 패드**.
설계규칙(보드에 저장): 트랙 최소 0.09 / 클리어런스 0.09 / 비아 0.4/0.2 — JLC 6층 표준 공정 내.

| 항목 | 상태 |
|---|---|
| 배치 | ✅ 겹침 0 · 보드이탈 0 · 배터리존 침범 0 (`check_pcb.py`) — J1/J2는 **Ø1.0 mm 원형 납땜 패드**(와이어 직납, 피치 1.8 mm) |
| GND | ✅ L2·L5 통짜 플레인 + F/B 포어 + 스티칭 비아, 고립 동박 0 |
| RF 급전 | ✅ ANT(U3.30)→FL1→pi 슬롯(C35/R15/C34)→칩안테나. **E1 은 데이터시트(36S0021A Rev4.1) 확정 4단자 랜드패턴** — 1·4 Feed(브리지 급전), 2·3 GND(좌측 포어로 0.3mm 스트립), 에지 수직 장착 |
| 신호 배선 | ✅ **전 넷 100% 배선 완료** (77넷, 미결선 0) — 자동배선 + 마지막 4링크(DCC/DEC1/ADC_P/CLAMP2)는 검증된 수동 경로(`close4.py`), 비아-인-패드 2곳(U3.47, C30) 포함 |
| DRC | ✅ `drc-report.txt`: **동박 위반 0** (미결선 0 · 클리어런스 0 · 홀 0 · 엣지 0 · 고립 0). 남은 항목은 실크 겹침/코트야드/서멀 릴리프 경고성 코스메틱 |

### 4층 축소 검토 결과 (2026-07-11)

**불가 판정.** 같은 배치(23.4×22 양면)에서 4층(배선층 3개: F/In2/B)으로 세 가지 전략
(프레시, 우선순위 선배선, AGGRO 립업)을 시도했으나 최소 12~22패드 미결선으로 수렴 실패.
U3 서쪽 핀 탈출과 U1 QFN 코너가 배선층 4개(6층)를 요구함. 4층을 원하면 보드를
~26×24 이상으로 키우고 단면 실장로 돌아가는 트레이드오프가 필요할 것으로 추정
(6층 유지 권장 — JLC 기준 6층 프로토 비용 차이는 수량 적을 때 크지 않음).

주의: 비아-인-패드 2곳(U3.47 DCC, C30.1 DEC1)과 U1.47 비아는 리플로우 시 솔더 위킹
가능성이 있음 — 프로토 수조립에서는 문제없고, 양산 시 via tenting/plugging 지정 권장.

### 남은 작업 (KiCad GUI에서 배선 불필요!)

1. 보드 열기 → `B`(존 채우기) → DRC 실행해 동박 위반 0 확인
2. 커스텀 풋프린트 랜드패턴 대조: **E1 완료**(36S0021A Rev4.1 반영) / FL1·코일 패드는 데이터시트 대조 남음
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
