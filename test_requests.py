import requests
import json
from time import sleep

header = {"Host": "fraud-classifier.kserve-deployments.example.com"}
body = [
    {
        "isFraud": 0,
        "TransactionAmt": 495.0,
        "ProductCD": "W",
        "card4": "visa",
        "P_emaildomain": "live.com",
        "R_emaildomain": None,
        "M1": "T",
        "M2": "T",
        "M3": "T",
    }
]

count = 0
for i in range(100):
    response = requests.post(
        "http://127.0.0.1/fraud-classifier", headers=header, data=json.dumps(body)
    )
    if response.json()[0] == 1:
        count += 1
    sleep(0.02)
XGB_score = int(count)
RF_score = int(100 - count)
print(f"{XGB_score}% to the XGB Model, {RF_score}% to the RF Model")
