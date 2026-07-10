# BOM

레퍼런스는 `hardware/kicad/discrete-potentiostat.kicad_sch`와 1:1 일치.

## IC / 모듈 / 배터리 / 기구

| Ref | 부품 | 패키지 | 역할 |
|---|---|---|---|
| U1 | BQ51013BRHLR | VQFN-20 (RHL) | Qi 수신, 레귤레이티드 5 V 출력 |
| U6 | MCP73832T-2ACI/OT | SOT-23-5 | 리튬 충전기 4.2 V / 20 mA, 종지 ~1.5 mA |
| U2 | MIC5205-3.3YM5 | SOT-23-5 | 3.3 V LDO |
| U3 | MDBT42Q-1MV2 (Raytac, nRF52832) | 41-pad 모듈 | MCU + BLE + SAADC |
| U4 | OPA2391xDGK | VSSOP-8 | A: 컨트롤 앰프 / B: TIA |
| U5 | REF35102QDBVR | SOT-23-6 | 1.024 V 레퍼런스 |
| BT1 | LIR2032 + SMD 홀더 (Keystone 3034 등) | — | 3.7 V / ~40 mAh (보호회로 없음 — 펌웨어 컷오프 필수) |
| L1 | Würth 760308101303 | Ø26.3 mm | Qi RX 코일, **47 µH** |
| D1 | LED GREEN 0603 | — | 상태 표시 (P0.07) |
| J1 | 1×3 헤더/FPC | 2.54 mm | 전극 CE/RE/WE |
| J2 | 1×5 헤더 | 1.27 mm | SWD (3V3/SWDIO/SWDCLK/RESET/GND) |
| TP1–TP3 | 테스트포인트 | Ø1.5 mm | VREF / TIA_OUT / VBIAS |

## 수동소자 (회로도 Ref 순)

| Ref | 값 | 사양 | 역할 |
|---|---|---|---|
| C1 | 56 nF | C0G 50 V, 1206 | 직렬 공진 (fs≈100 kHz, L=47 µH 기준) |
| C2 | 560 pF | C0G 50 V | 병렬 공진 (fd≈1 MHz) |
| C3, C4 | 10 nF | 25 V | BOOT1/BOOT2 → AC1/AC2 |
| C5, C6 | 22 nF | 25 V | COMM1/COMM2 → AC1/AC2 |
| C7, C8 | 470 nF | 25 V | CLAMP1/CLAMP2 → AC1/AC2 |
| C9 | 10 µF | 25 V, 0805 | RECT |
| C10, C11 | 4.7 µF + 100 nF | 10 V | V5OUT (BQ51013B OUT = MCP73832 입력) |
| C12 | 1 µF | 10 V | LDO 입력 |
| C13 | 2.2 µF | 10 V | LDO 출력 (세라믹, ≥1 µF) |
| C14 | 470 pF | — | MIC5205 BYP (노이즈 저감) |
| C15 | 100 nF | — | VBAT_DIV 필터 |
| C16, C17 | 100 nF + 1 µF | — | AFE_PWR 디커플링 |
| C18 | 1 µF | 저누설 | REF35 NR (2.7 Hz LPF) |
| C19 | 100 nF | — | VREF 출력 (REF35 최소 0.1 µF) |
| C20 | 100 nF | — | VBIAS(0.512 V) 필터 |
| C21 | 1 nF | **DNP** | 컨트롤 앰프 보상 (발진 시만 실장) |
| C22 | 470 pF | C0G | CF — TIA 대역제한 (RF 교체 시 짝 교체) |
| C23, C24 | 10 nF | C0G | SAADC 입력 RC |
| C25, C26 | 10 µF + 100 nF | 10 V | 모듈 VDD |
| C27 | 4.7 µF | 10 V | 배터리(+BATT) 측 |
| R1 | 2.32 kΩ | 1% | ILIM 상단 — IMAX = 250/(R1+R2) ≈ 100 mA (5V 출력 전류 한도) |
| R2 | 200 Ω | 1% | RFOD (FOD 탭, 캘리브레이션 시작값) |
| R3 | 49.9 kΩ | 1% | MCP73832 PROG — ICHG = 1000/RPROG = 20 mA |
| R4 | 100 kΩ | — | /CHG 풀업 → 3V3 |
| R5, R6 | 1 MΩ ×2 | 1% | VBAT 모니터 분압 → AIN2 |
| R7, R8 | 1 MΩ ×2 | 1% 페어 | 1:1 바이어스 분압 → VBIAS 0.512 V |
| R9 | 100 Ω | — | CE 직렬 (안정성) |
| R10 | 1 kΩ | **DNP** | 컨트롤 앰프 보상 (C21과 직렬) |
| R11 | 1 MΩ | 0.1% thin-film, ≤50 ppm/°C | **RF** (5.1 M/10 M 교체 풋프린트) |
| R12, R13 | 1 kΩ ×2 | — | SAADC 입력 RC |
| R14 | 1 kΩ | — | LED 직렬 |
| TH1 | NTC 10 kΩ | B≈3380 | BQ51013B TS/CTRL — LIR2032 홀더 밀착 배치 |

## 참고

- RF/CF 교체 옵션: 1 M/470 p(기본) · 5.1 M/100 p · 10 M/47 p — 펌웨어 `rf_ohm`/`oversample`은 런타임 설정.
- OPA2391 대체: OPA2392 (동급 상위).
- 충전 체인은 2단(Qi 5 V → 저전류 충전기): BQ51050B 직접충전이 TI 공식으로 <200 mA 비권장이라 교체됨 (`design.md` §5).
- 초소형화 시 동일 토폴로지로 BQ51003(DSBGA-28) + BQ25100(DSBGA-6) 교체 가능.
