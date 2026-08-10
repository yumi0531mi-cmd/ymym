"""
AI Score Engine V6
작성자 : ChatGPT

역할
-----
각 항목을 점수화하여 100점 만점 AI 점수를 계산한다.

현재 버전 : V6.0
"""

from dataclasses import dataclass


@dataclass
class ScoreResult:
    total: int
    grade: str
    detail: dict


class AIScoreEngine:

    def __init__(self):

        self.weight = {

            "news":20,
            "volume":15,
            "chart":15,
            "vwap":10,
            "momentum":10,
            "sec":15,
            "short":5,
            "market":5,
            "sector":5,
        }

    def calculate(self,data):

        detail={}

        total=0

        for key,max_score in self.weight.items():

            score=min(data.get(key,0),max_score)

            detail[key]=score

            total+=score

        if total>=90:
            grade="S"

        elif total>=80:
            grade="A"

        elif total>=70:
            grade="B"

        elif total>=60:
            grade="C"

        else:
            grade="D"

        return ScoreResult(
            total=total,
            grade=grade,
            detail=detail
        )


if __name__=="__main__":

    engine=AIScoreEngine()

    sample={

        "news":18,
        "volume":13,
        "chart":14,
        "vwap":9,
        "momentum":9,
        "sec":13,
        "short":4,
        "market":5,
        "sector":5

    }

    result=engine.calculate(sample)

    print(result)