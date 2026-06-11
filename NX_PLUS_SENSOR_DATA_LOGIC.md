# NX+ Sensor Data Collection Logic

이 문서는 `server_viewer_tabs_ubuntu.py`에서 NX+ 센서 데이터를 로그 기반으로 가져오는 로직을 정리한 것이다.

## 목적

NX+는 샷 데이터가 로그와 `.grc` 파일명, `NXShotData_YYYY.MM.DD.csv`에 나뉘어 기록된다. 로그와 `.grc` 파일명에는 `Dynamic Loft`, `Face Angle`, `Club Path`처럼 필요한 세부 값이 모두 들어있지 않다.

따라서 현재 구현은 NX+ 로그에서 정상 샷 감지와 DB 저장 여부만 확인하고, 실제 샷 데이터는 `NXShotData_YYYY.MM.DD.csv`에서 읽는다.

## 대상 파일

- 로그 폴더 기본값: `/home/golfzon/.nxsensor/log`
- 대상 로그 파일명: `NXSensorLog_YYYYMMDD*.log`
- 샷 데이터 CSV 예시: `NXShotData_2026.04.27.csv`
- GRC 저장 경로 예시: `/home/golfzon/.nxsensor/grc/2026.04.27/`

## 확인된 로그 패턴

`NXSensorLog_20260427_0.log`와 `NXShotData_2026.04.27.csv` 기준으로 정상 저장된 14개 샷에서 아래 로그가 반복적으로 확인됐다.

```text
CGzStateDetectTrigger: GZ_EVENT_BALLIMPACT_TV
[NOTI_2ND] ball_velocity ... ball_incidence ... ball_direction ... club_velocity ... club_incidence ... club_direction ...
[NOTI_2ND-TV] spinaxis ..., backspin ... sidespin ... totalspin ...
[NOTI_2ND-Legacy] ...
[NOTI_2ND-AI] ...
CGzStateSaveShotDB::SaveShotDB done
=====================> SHOTDB #: N
ShotDB packed: /home/golfzon/.nxsensor/grc/YYYY.MM.DD/....grc
```

집계 결과:

| 로그 종류 | 개수 | 의미 |
|---|---:|---|
| `GZ_EVENT_BALLIMPACT_TV` | 22 | 정상 샷과 Cancel 샷 모두에서 발생 |
| `[NOTI_2ND]` | 14 | 정상 샷마다 1회 |
| `[NOTI_2ND-TV]` | 14 | 정상 샷마다 1회 |
| `[NOTI_2ND-Legacy]` | 14 | 정상 샷마다 1회 |
| `[NOTI_2ND-AI]` | 14 | 정상 샷마다 1회 |
| `CGzStateSaveShotDB::SaveShotDB done` | 14 | 정상 샷마다 1회 |
| `SHOTDB #:` non-Cancel | 14 | 정상 샷마다 1회 |
| `ShotDB packed:` | 14 | 정상 샷마다 1회 |
| `SHOTDB #: ... - Cancel` | 8 | 취소 샷 |

## CSV 저장 타이밍

로그에는 `NXShotData_2026.04.27.csv` 저장 완료를 직접 나타내는 문구는 없다. 다만 CSV의 `ShotDB Name` 값이 로그의 `grc data path` 및 `ShotDB packed` 파일명과 14개 모두 매칭됐다.

마지막 샷 기준:

```text
12:07:15.433  grc data path
12:07:15.680  NXShotData_2026.04.27.csv LastWriteTime
12:07:15.684  CGzStateSaveShotDB::SaveShotDB done
12:07:15.688  CGzStateSaveShotDB::Exit
12:07:15.722  ShotDB packed
```

따라서 CSV 행 저장은 `CGzStateSaveShotDB::SaveShotDB done` 직전 또는 그 시점에 완료되는 것으로 추정된다. 현재 로직은 `ShotDB packed`를 정상 저장 완료 신호와 CSV 행 식별자로 사용한 뒤, 실제 값은 CSV에서 읽는다.

## 안정적인 정상 샷 판단 조건

`GZ_EVENT_BALLIMPACT_TV`는 Cancel 샷에도 나오므로 단독 트리거로 사용하면 오탐이 생긴다.

권장 조건:

```text
[NOTI_2ND] 수신
→ [NOTI_2ND-TV] 수신
→ CGzStateSaveShotDB::SaveShotDB done
→ SHOTDB #: N  // Cancel 아님
→ CGzStateSaveShotDB::Exit
→ ShotDB packed
```

현재 구현은 `SaveShotDB done` 이후 `SHOTDB #:`가 `Cancel`이 아닌지 확인하고, `Exit` 이후 `ShotDB packed`를 기다린다.

## 데이터 추출 위치

`ShotDB packed` 로그의 `.grc` 파일명은 CSV 행을 찾기 위한 식별자로 사용한다. 실제 데이터는 같은 날짜 폴더의 `NXShotData_YYYY.MM.DD.csv`에서 읽는다.

예시:

```text
/home/golfzon/.nxsensor/grc/2026.04.27/0000_3faf3577_2026.04.27_11.56.51_S_D_R_B57.32&17.34&3.90_C43.69&2.46&-2.81_S3718.15&130.01&3720.42.grc
```

위 packed 경로에서 CSV 파일 경로를 만든다.

```text
/home/golfzon/.nxsensor/grc/2026.04.27/NXShotData_2026.04.27.csv
```

그리고 `.grc` 확장자를 제거한 파일명을 CSV의 `ShotDB Name`과 매칭한다.

```text
ShotDB Name =
0000_3faf3577_2026.04.27_11.56.51_S_D_R_B57.32&17.34&3.90_C43.69&2.46&-2.81_S3718.15&130.01&3720.42
```

CSV에서 읽는 주요 필드:

| CSV 컬럼 | 내부 필드 |
|---|---|
| `ShotDB #` | `shotdb_id`, `shot_id` |
| `Date` | `timestamp` |
| `ShotDB Name` | `shotdb_name` |
| `Ball Speed` | `ball_velocity` |
| `Ball Incidence` | `ball_incidence` |
| `Ball Direction` | `ball_direction` |
| `Club Speed` | `club_velocity` |
| `Attack Angle` | `attack_angle`, `club_incidence` |
| `Club Path` | `club_path`, `club_direction` |
| `Face Angle` | `face_angle` |
| `Dynamic Loft` | `dynamic_loft` |
| `Back Spin` | `backspin` |
| `Side Spin` | `sidespin` |
| `Total Spin` | `totalspin` |
| `Spin Axis` | `spin_axis` |
| `Club Type` | `club_type` |
| `Mat Type` | `mat_type` |

## NOTI 값과 CSV 값 비교

정상 샷 14개 기준으로 `[NOTI_2ND]`, `[NOTI_2ND-TV]` 값과 `NXShotData_2026.04.27.csv`의 주요 값은 모두 일치했다. 로그의 NOTI 값은 소수점 2자리 중심이고 CSV 값은 더 정밀하므로 비교에는 작은 허용 오차를 둔다.

비교 대상:

```text
ball_velocity
ball_incidence
ball_direction
club_velocity
club_incidence
club_direction
backspin
sidespin
totalspin
```

코드에서는 이 비교 결과를 `noti_match`에 저장한다.

## 앱 내부 매핑

NX+ 원본 필드와 Viewer 비교표 필드 매핑:

| NX+ 필드 | Viewer 표시 |
|---|---|
| `ball_velocity` | `Ball Speed` |
| `ball_incidence` | `Launch Angle` |
| `sidespin` | `Side Spin` |
| `backspin` | `Back Spin` |
| `totalspin` | `Total Spin` |

서버 내부 공통 필드:

| 내부 필드 | 값 |
|---|---|
| `source_system` | `NX+` |
| `source_format` | `nx_csv_after_log` |
| `ball_speed` | CSV `Ball Speed` |
| `launch_angle` | CSV `Ball Incidence` |
| `spin_rate` | CSV `Total Spin` |

## Viewer 표시 항목 선택

Viewer 탭의 `Select Metrics` 버튼에서 비교표에 노출할 값을 선택할 수 있다. 선택한 항목은 `viewer.ini`의 `selected_metrics`에 저장되며, 다음 실행 시에도 유지된다.

기본 표시 항목:

```text
Ball Speed
Launch Angle
Side Spin
Back Spin
Total Spin
```

선택 가능한 주요 항목:

```text
Club
Club Type
Ball Speed
Launch Angle
Ball Direction
Club Speed
Attack Angle
Club Path
Face Angle
Dynamic Loft
Side Spin
Back Spin
Total Spin
Spin Axis
Carry
Total Distance
Offline
Peak Height
Descent Angle
Mat Type
```

## 구현 흐름

1. Server 탭에서 `NX+ Log Dir`를 설정한다.
2. `Start NX+ Monitor`를 누르면 `NxLogMonitorThread`가 시작된다.
3. 오늘 날짜의 `NXSensorLog_YYYYMMDD*.log` 중 가장 최근 수정된 파일을 찾는다.
4. 로그 파일 끝으로 이동한 뒤 tail 방식으로 새 줄만 읽는다.
5. `[NOTI_2ND]`를 만나면 Ball/Club 값을 임시 저장한다.
6. `[NOTI_2ND-TV]`를 만나면 Spin 값을 합쳐 최신 NOTI 값으로 저장한다.
7. `CGzStateSaveShotDB::SaveShotDB done`을 만나면 현재 샷 저장 흐름을 추적한다.
8. `SHOTDB #:`가 `Cancel`이면 해당 샷은 무시한다.
9. `CGzStateSaveShotDB::Exit` 이후 `ShotDB packed`를 최대 10초 기다린다.
10. `ShotDB packed` 경로에서 날짜 폴더와 `ShotDB Name`을 얻는다.
11. 같은 날짜 폴더의 `NXShotData_YYYY.MM.DD.csv`를 읽는다.
12. CSV에서 `ShotDB Name`이 같은 행을 찾는다.
13. CSV 저장 타이밍 차이를 고려해 최대 10회 재시도한다.
14. CSV 행에서 NX+ 샷 데이터를 만든다.
15. NOTI 값과 CSV 값을 비교해 `noti_match`를 기록한다.
16. `latest_shot.json`에 최신 샷을 저장한다.
17. `shot_history.csv`에 이력을 append한다.
18. SSE로 Viewer에 `shot` 이벤트를 발행한다.
19. Viewer의 `GCQuad | NX+` 비교표에서 NX+ 컬럼을 갱신한다.

## ShotMerge 저장과 중복 방지

GCQuad와 NX+ 데이터는 각각 독립적으로 들어오므로, `DateMerge_NXPlus_GCQuad.csv` 저장은 최근 수신된 양쪽 데이터를 시간 기준으로 묶어서 처리한다.

기본 동작은 Server 탭의 `ShotMerge` 체크박스가 켜진 상태이며, 이때는 GCQuad와 NX+ 양쪽 데이터가 모두 있어야 저장한다. 한쪽 데이터가 먼저 들어오고 `Wait Seconds` 값, 기본 10초, 안에 다른 쪽 데이터가 들어오면 정상적인 한 샷으로 판단해 DateMerge CSV를 append하고 NX+ `.grc` 파일을 `grc` 폴더로 복사한다. `grc-decrypt-cli`가 있으면 같은 시점에 `grc_decrypted` 폴더로 복호화도 수행한다.

중복 저장을 막기 위해 세 종류의 key를 관리한다.

| key 종류 | 기준 |
|---|---|
| merge pair key | GCQuad key와 NX+ key의 조합 |
| GCQuad source key | `shot_id` 또는 `timestamp` + `received_at` |
| NX+ source key | `shotdb_name`, 없으면 `shotdb_filename`, `shotdb_id`, `shot_id` 순서 |

GCQuad의 `shot_id`는 FSX 세션이 바뀌면 `1, 2, 3...`처럼 다시 시작될 수 있다. 그래서 GCQuad source key는 단순 `shot_id`만 쓰지 않고 `shot_id|received_at` 형태를 우선 사용한다. 이렇게 해야 이전 테스트의 `GCQ 4`와 새 테스트의 `GCQ 4`가 같은 샷으로 오판되어 저장이 막히는 문제를 피할 수 있다.

앱 시작 시 이미 존재하는 `DateMerge_NXPlus_GCQuad.csv`를 읽어서 기존 pair key, GCQuad source key, NX+ source key를 미리 `saved_merge_keys`, `saved_gcq_merge_keys`, `saved_nx_merge_keys`에 적재한다. 이후 새 샷 저장 시 다음 조건 중 하나라도 해당하면 append하지 않는다.

- 같은 GCQuad/NX+ pair key가 이미 저장됨
- 같은 GCQuad source key가 이미 저장됨
- 같은 NX+ source key가 이미 저장됨

이 로직은 과거에 발생했던 `GCQ 4 - NX 3`, `GCQ 4 - NX 4`, `GCQ 5 - NX 4`, `GCQ 5 - NX 5` 같은 연쇄 중복 저장을 막기 위한 것이다. 한 장비의 source key는 한 번 저장되면 다음 샷 매칭에 재사용하지 않는다.

SSE 이벤트로 같은 샷이 화면에 먼저 표시되는 경우도 별도로 처리한다. `apply_latest_data(..., record_receive=False)`로 들어온 SSE/Refresh 데이터는 Viewer 표시만 갱신하고 ShotMerge 저장 로직은 실행하지 않는다. 이후 실제 수신 경로의 `record_receive=True` 데이터가 같은 signature로 들어와도 저장 로직이 실행되도록, 중복 signature early-return은 `record_receive=False`일 때만 적용한다.

GRC 복사는 NX+ 데이터의 `shotdb_path`를 우선 사용한다. 만약 `shotdb_path`가 비어 있고 `shotdb_filename`과 `nx_csv_file`이 있으면, `nx_csv_file`의 폴더와 `shotdb_filename`을 조합해 `.grc` 경로를 재구성한 뒤 복사를 시도한다. DateMerge의 `Date-Time(GCQ)`는 FSX JSON의 내부 `Timestamp`가 아니라 프로그램이 실제 수신한 `received_at`을 우선 기록한다.

`ShotMerge` 체크박스를 끄면 양쪽 데이터가 모두 들어오지 않아도 저장할 수 있다. 이 경우 한쪽 데이터가 들어온 뒤 `Wait Seconds` 안에 counterpart가 들어오지 않으면 단일 샷으로 DateMerge CSV를 저장하고, NX+ 데이터가 있는 경우에는 GRC 복사도 수행한다. 이 체크 상태는 ini에 저장하지 않고 프로그램 시작 시 항상 체크 상태로 초기화한다.

## 예외 처리

| 상황 | 처리 |
|---|---|
| 오늘 로그 파일 없음 | 대기 후 재시도 |
| 로그 파일 truncate | 파일을 다시 열고 처음부터 읽음 |
| `SHOTDB #: ... - Cancel` | 샷으로 처리하지 않음 |
| `ShotDB packed` 중복 | 이미 본 packed path는 무시 |
| `Exit` 이후 packed 없음 | 10초 후 timeout 로그 출력 |
| NOTI 값 없음 | 정상 샷 조건 미충족으로 무시 |
| CSV 파일 없음 | 재시도 후 실패 로그 출력 |
| CSV 행 없음 | `ShotDB Name` 및 `ShotDB #` 기준 재시도 후 실패 로그 출력 |

## 결론

NX+ 센서 데이터는 로그만으로 모든 값을 얻기 어렵다. 특히 `Dynamic Loft`, `Face Angle`, `Club Path` 같은 값은 `NXShotData_YYYY.MM.DD.csv`에서 읽어야 한다.

따라서 로그는 정상 샷 감지와 DB 저장 여부 확인에 사용하고, 데이터 추출은 CSV 행 기준으로 수행한다. `GZ_EVENT_BALLIMPACT_TV`는 Cancel 샷에도 발생하므로 트리거로 부적합하고, `[NOTI_2ND]` + `[NOTI_2ND-TV]` + `SaveShotDB done` + non-Cancel `SHOTDB #` + `ShotDB packed` 조합이 정상 샷 판단에 적합하다.
