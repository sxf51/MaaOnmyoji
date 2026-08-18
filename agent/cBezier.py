'''
author: cbb
这个库基于贝塞尔曲线实现模拟人手动滑动的轨迹
可以用于selenium轨迹的模拟，或者生成轨迹数组用于js加密通过网站服务器后端分控检测
QQ群 134064772
里面的人说话好听，个个都是人才
'''
import numpy as np
import math
import random


def bezier_points(control_points, segments=20, easing=True):
    """将任意阶贝塞尔曲线采样为 ``segments`` 段直线。

    返回值包含起点和终点，因此长度为 ``segments + 1``。这里使用
    De Casteljau 算法，不依赖曲线是否水平，也规避了旧实现以 x 为自变量时
    无法处理竖直滑动的问题。
    """
    if not isinstance(segments, int) or segments < 1:
        raise ValueError("segments 必须是正整数")
    if len(control_points) < 2:
        raise ValueError("control_points 至少需要起点和终点")

    points = [np.asarray(point, dtype=np.float64) for point in control_points]
    if any(point.shape != (2,) for point in points):
        raise ValueError("每个控制点都必须是 [x, y]")

    result = []
    for index in range(segments + 1):
        t = index / segments
        if easing:
            # smoothstep：起步和收尾更慢，中间更快。
            t = t * t * (3 - 2 * t)
        work = [point.copy() for point in points]
        while len(work) > 1:
            work = [(1 - t) * left + t * right for left, right in zip(work, work[1:])]
        result.append((int(round(work[0][0])), int(round(work[0][1]))))

    result[0] = tuple(map(int, control_points[0]))
    result[-1] = tuple(map(int, control_points[-1]))
    return result


def swipe_control_points(start, end, deviation=30.0, bend=1.0):
    """为一次滑动生成三次贝塞尔控制点。

    中间两个点沿直线法向偏移；``deviation`` 控制弯曲像素数，正负值控制
    弯曲方向，``bend`` 可用于整体缩放弯曲程度。
    """
    start = np.asarray(start, dtype=np.float64)
    end = np.asarray(end, dtype=np.float64)
    delta = end - start
    length = float(np.linalg.norm(delta))
    if length == 0:
        raise ValueError("滑动起点和终点不能相同")
    normal = np.array([-delta[1], delta[0]]) / length
    offset = normal * float(deviation) * float(bend)
    return [start, start + delta / 3 + offset, start + delta * 2 / 3 + offset, end]


class bezierTrajectory:

    def _bztsg(self, dataTrajectory):
        lengthOfdata = len(dataTrajectory)

        def staer(x):
            t = ((x - dataTrajectory[0][0]) / (dataTrajectory[-1][0] - dataTrajectory[0][0]))
            y = np.array([0, 0], dtype=np.float64)
            for s in range(len(dataTrajectory)):
                y += dataTrajectory[s] * ((math.factorial(lengthOfdata - 1) / (
                            math.factorial(s) * math.factorial(lengthOfdata - 1 - s))) * math.pow(t, s) * math.pow(
                    (1 - t), lengthOfdata - 1 - s))
            return y[1]

        return staer

    def _type(self, type, x, numberList):
        numberListre = []
        pin = (x[1] - x[0]) / numberList
        if type == 0:
            for i in range(numberList):
                numberListre.append(i * pin)
            if pin >= 0:
                numberListre = numberListre[::-1]
        elif type == 1:
            for i in range(numberList):
                numberListre.append(1 * ((i * pin) ** 2))
            numberListre = numberListre[::-1]
        elif type == 2:
            for i in range(numberList):
                numberListre.append(1 * ((i * pin - x[1]) ** 2))

        elif type == 3:
            dataTrajectory = [np.array([0,0]), np.array([(x[1]-x[0])*0.8, (x[1]-x[0])*0.6]), np.array([x[1]-x[0], 0])]
            fun = self._bztsg(dataTrajectory)
            numberListre = [0]
            for i in range(1,numberList):
                numberListre.append(fun(i * pin) + numberListre[-1])
            if pin >= 0:
                numberListre = numberListre[::-1]
        numberListre = np.abs(np.array(numberListre) - max(numberListre))
        biaoNumberList = ((numberListre - numberListre[numberListre.argmin()]) / (
                    numberListre[numberListre.argmax()] - numberListre[numberListre.argmin()])) * (x[1] - x[0]) + x[0]
        biaoNumberList[0] = x[0]
        biaoNumberList[-1] = x[1]
        return biaoNumberList

    def getFun(self, s):
        '''

        :param s: 传入P点
        :return: 返回公式
        '''
        dataTrajectory = []
        for i in s:
            dataTrajectory.append(np.array(i))
        return self._bztsg(dataTrajectory)

    def simulation(self, start, end, le=1, deviation=0, bias=0.5):
        '''

        :param start:开始点的坐标 如 start = [0, 0]
        :param end:结束点的坐标 如 end = [100, 100]
        :param le:几阶贝塞尔曲线，越大越复杂 如 le = 4
        :param deviation:轨迹上下波动的范围 如 deviation = 10
        :param bias:波动范围的分布位置 如 bias = 0.5
        :return:返回一个字典equation对应该曲线的方程，P对应贝塞尔曲线的影响点
        '''
        start = np.array(start)
        end = np.array(end)
        cbb = []
        if le != 1:
            e = (1 - bias) / (le - 1)
            cbb = [[bias + e * i, bias + e * (i + 1)] for i in range(le - 1)]

        dataTrajectoryList = [start]

        t = random.choice([-1, 1])
        w = 0
        for i in cbb:
            px1 = start[0] + (end[0] - start[0]) * (random.random() * (i[1] - i[0]) + (i[0]))
            p = np.array([px1, self._bztsg([start, end])(px1) + t * deviation])
            dataTrajectoryList.append(p)
            w += 1
            if w >= 2:
                w = 0
                t = -1 * t

        dataTrajectoryList.append(end)
        return {"equation": self._bztsg(dataTrajectoryList), "P": np.array(dataTrajectoryList)}

    def trackArray(self, start, end, numberList, le=1, deviation=0, bias=0.5, type=0, cbb=0, yhh=10):
        '''

        :param start:开始点的坐标 如 start = [0, 0]
        :param end:结束点的坐标 如 end = [100, 100]
        :param numberList:返回的数组的轨迹点的数量 numberList = 150
        :param le:几阶贝塞尔曲线，越大越复杂 如 le = 4
        :param deviation:轨迹上下波动的范围 如 deviation = 10
        :param bias:波动范围的分布位置 如 bias = 0.5
        :param type:0表示均速滑动，1表示先慢后快，2表示先快后慢，3表示先慢中间快后慢 如 type = 1
        :param cbb:在终点来回摆动的次数
        :param yhh:在终点来回摆动的范围
        :return:返回一个字典trackArray对应轨迹数组，P对应贝塞尔曲线的影响点
        '''
        s = []
        fun = self.simulation(start, end, le, deviation, bias)
        w = fun['P']
        fun = fun["equation"]
        if cbb != 0:
            numberListOfcbb = round(numberList*0.2/(cbb+1))
            numberList -= (numberListOfcbb*(cbb+1))

            xTrackArray = self._type(type, [start[0], end[0]], numberList)
            for i in xTrackArray:
                s.append([i, fun(i)])
            dq = yhh/cbb
            kg = 0
            ends = np.copy(end)
            for i in range(cbb):
                if kg == 0:
                    d = np.array([end[0] + (yhh-dq*i), ((end[1]-start[1])/(end[0]-start[0]))*(end[0]+(yhh-dq*i)) +(end[1]-((end[1]-start[1])/(end[0]-start[0]))*end[0])   ])
                    kg = 1
                else:
                    d = np.array([end[0] - (yhh - dq * i), ((end[1]-start[1])/(end[0]-start[0]))*(end[0]-(yhh-dq*i)) +(end[1]-((end[1]-start[1])/(end[0]-start[0]))*end[0])  ])
                    kg = 0
                print(d)
                y = self.trackArray(ends, d, numberListOfcbb, le=2, deviation=0, bias=0.5, type=0, cbb=0, yhh=10)
                s += list(y['trackArray'])
                ends = d
            y = self.trackArray(ends, end, numberListOfcbb, le=2, deviation=0, bias=0.5, type=0, cbb=0, yhh=10)
            s += list(y['trackArray'])

        else:
            xTrackArray = self._type(type, [start[0], end[0]], numberList)
            for i in xTrackArray:
                s.append([i, fun(i)])
        return {"trackArray": np.array(s), "P": w}


