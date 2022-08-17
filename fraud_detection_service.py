import numpy as np
import pandas as pd

import bentoml
from bentoml.io import PandasDataFrame, JSON

model_type = "xgb"
# model_type = "rf"

ohe_encoder = bentoml.models.get(f"fraud_classifier_{model_type}:latest").custom_objects[
    "ohe_encoder"
]
fraud_classifier_runner = bentoml.sklearn.get(f"fraud_classifier_{model_type}:latest").to_runner()

svc = bentoml.Service("fraud_classifier", runners=[fraud_classifier_runner])


@svc.api(input=PandasDataFrame(), output=JSON(), route="/fraud-classifier")
def predict(df: pd.DataFrame) -> np.ndarray:
    X = df[["ProductCD", "P_emaildomain", "R_emaildomain", "card4", "M1", "M2", "M3"]]
    X = X.fillna(pd.NA)  # ensure all missing values are pandas NA
    X = pd.DataFrame(
        ohe_encoder.transform(X).toarray(),
        columns=ohe_encoder.get_feature_names_out().reshape(-1),
    )
    X["TransactionAmt"] = df[["TransactionAmt"]].to_numpy()
    return fraud_classifier_runner.predict.run(X)
