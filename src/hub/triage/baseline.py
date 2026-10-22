"""The baseline every LLM approach has to beat: TF-IDF on word and character n-grams, logistic regression.
Trains in seconds on a CPU, answers in microseconds, and gives a calibrated-enough confidence to cascade on."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline

from hub.triage.data import Message


@dataclass
class Prediction:
    intent: str
    confidence: float


class TfidfBaseline:
    def __init__(self, c: float = 8.0) -> None:
        features = FeatureUnion(
            [
                ("words", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
                ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=3, sublinear_tf=True)),
            ]
        )
        self.model = Pipeline([("tfidf", features), ("lr", LogisticRegression(C=c, max_iter=2000))])

    def fit(self, train: list[Message]) -> TfidfBaseline:
        self.model.fit([m.text for m in train], [m.intent for m in train])
        return self

    def predict(self, texts: list[str]) -> list[Prediction]:
        proba = self.model.predict_proba(texts)
        classes = self.model.classes_
        best = np.argmax(proba, axis=1)
        return [Prediction(str(classes[i]), float(p[i])) for i, p in zip(best, proba, strict=True)]
