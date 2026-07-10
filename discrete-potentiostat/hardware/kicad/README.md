# KiCad 회로도

`discrete-potentiostat.kicad_sch` — KiCad 7 포맷 (KiCad 7/8/9에서 열림), 심볼 전부 파일에 내장(라이브러리 설치 불필요).
`discrete-potentiostat.pdf` — 렌더링본.

## 검증 상태 (Rev A)

| 항목 | 상태 |
|---|---|
| 넷 연결 | ✅ **설계 넷리스트 37개 vs `kicad-cli` 추출 넷리스트 완전 일치** (`generator/check_nets.py`) |
| BQ51050BRHL 핀맵 | ✅ KiCad 공식 라이브러리 심볼 (데이터시트 대조 검수본) |
| MIC5205 핀맵 | ✅ KiCad 공식 라이브러리 (1 IN / 2 GND / 3 EN / 4 BYP / 5 OUT) |
| OPA2391 DGK 핀맵 | ✅ 데이터시트 확인 (표준 듀얼: 1 OUTA / 2 −INA / 3 +INA / 4 V− / 5 +INB / 6 −INB / 7 OUTB / 8 V+) |
| REF35102 DBV 핀맵 | ✅ 데이터시트 확인 (1,2 GND / 3 EN / 4 VIN / 5 NR / 6 VREF) |
| MDBT42Q 패드 1–21, 37–41 | ✅ 데이터시트 교차검증 |
| MDBT42Q 패드 22–36 | ⚠️ 순차 재구성 (P0.09/NFC1…P0.23) — **PCB 전에 Raytac 데이터시트 원본으로 대조** |
| 충전전류 공식 | ✅ IBULK = 314/RILIM (A·Ω), TERM 240 Ω/% |

**설계상 사용하는 모듈 패드는 전부 검증된 패드만 배정** (AIN0/1/2=15/16/17, P0.06/07/08=19/20/21, SWD=37/38, VDD=11, GND=1/12/40). 미검증 구간(22–36)에서 실제 연결된 것은 GND(30)와 SWD 헤더의 RESET(35)뿐이고, 둘 다 틀려도 동작을 깨지 않는 위치다(RESET 없이도 SWD 프로그래밍 가능).

## 핀 할당 (nRF52832)

| 기능 | 핀 | 모듈 패드 |
|---|---|---|
| SAADC 차동 N (VREF 1.024 V) | P0.02/AIN0 | 15 |
| SAADC 차동 P (TIA 출력) | P0.03/AIN1 | 16 |
| VBAT 모니터 | P0.04/AIN2 | 17 |
| AFE 전원 (high-drive) | P0.06 | 19 |
| 상태 LED | P0.07 | 20 |
| 충전 상태 (/CHG, 100k 풀업) | P0.08 | 21 |

## 재생성 방법

```bash
sudo apt install kicad kicad-symbols   # 공식 심볼 라이브러리 필요
cd generator
python3 extract_syms.py                # 시스템 라이브러리에서 베이스 심볼 추출
python3 gen_sch.py                     # ../discrete-potentiostat.kicad_sch 생성
kicad-cli sch export netlist --output out.net ../discrete-potentiostat.kicad_sch
python3 check_nets.py                  # 넷리스트 일치 확인 (RESULT: PASS 확인)
```

배치를 바꾸려면 `gen_sch.py`의 `layout()` 좌표를 수정하거나, KiCad GUI에서 직접 옮기면 된다
(연결은 라벨 기반이라 심볼을 옮겨도 넷은 유지됨).

## 회로도에 박혀 있는 주의사항 (DESIGN NOTES 텍스트 블록)

1. 충전전류 20 mA는 BQ51050B 권장 범위(≥200 mA) 밖 — 셀 밀봉 전 충전 프로파일 실측 필수, 대안 BQ51003+BQ25100
2. 공진탱크는 L=47 µH 기준 — 코일 변경 시 C1/C2 재계산
3. MDBT42Q 패드 22–36 재구성 — PCB 전 대조
4. WE 노드 가드링(VREF 구동), 플럭스 세정
5. R10/C21(보상망)은 DNP — CE-RE 루프 발진 시만 실장
6. LIR2032 무보호 — 3.0 V 미만 System OFF 펌웨어 필수
