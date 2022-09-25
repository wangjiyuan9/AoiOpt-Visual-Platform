import numpy as np
import time

from visual_plat.platform import VisualPlatform
from visual_plat.global_proxy.update_proxy import UpdateProxy


def update_task():
    index = 0
    while True:
        data = np.zeros((5, 5), dtype=np.int)
        data[int(index / 5)][index % 5] = 1
        UpdateProxy.reload("aoi", data)
        index += 1
        if index >= 25:
            index = 0
        time.sleep(1)


if __name__ == '__main__':
    VisualPlatform.launch(async_task=update_task)
