"""1분 주기 스캔 루프. tick() 실제 로직(D-7 확인/발사/만료)은 T7.
참조: docs/reference/backend-pipeline.md#스케줄러-workersschedulerpy--매-60초-tick

지금은 컨테이너가 죽지 않고 떠 있게만 하는 자리표시자.
"""

import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scheduler")


def tick() -> None:
    logger.info("tick stub - T7에서 실제 로직으로 교체")


def main() -> None:
    while True:
        tick()
        time.sleep(60)


if __name__ == "__main__":
    main()
