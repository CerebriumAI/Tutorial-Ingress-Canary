# Deployment 2.0: A/B Testing and Canaries with Seldon Core
In this tutorial, we will build on our previous deployment tutorial and augment our cluster an ingress and add the ability to conduct A/B testing and canary deployments.

When we deploy ML models, we are often unsure of the performance of our model in the real world. Methods like A/B testing and canary deployments allow us to evaluate the performance of our model and avoid erroneous deployments where the model is not performing as expected. This is done by splitting traffic between two ML services, typically your *baseline* model and a new model.

We are going to use two core technologies to implement A/B testing and canary deployments, [Istio](https://istio.io) and [Seldon Core](https://www.seldon.io/solutions/open-source-projects/core). Briefly, Istio is a service mesh that will supply an ingress controller, while Seldon Core is a service that will create deployments on our K8s cluster serve our models through Istio. It is worth noting that you could use [Ambassador](https://www.getambassador.io) as your ingress controller instead of Istio, but we ran into issues with Ambassador as Seldon Core only supports v1 of the Ambassador API which is quite outdated and difficult to install.

Prerequisites:
- Install Minikube
- Install BentoML
- Previous Deployment Tut

By the end of this tutorial you will be able to:
- Setup Istio for ingress
- Setup Seldon Core
- Run an A/B test or Canary Deployment with Seldon Core
  
This tutorial assumes you have done the previous tutorial on BentoML and [Minikube](https://minikube.sigs.k8s.io/docs/start/). If you wish to use a managed cloud cluster go for it, though you will need to pay for additional resources for Istio (it requires 4GB RAM)! Download the data required for this tutorial from [here](https://drive.google.com/file/d/1MidRYkLdAV-i0qytvsflIcKitK4atiAd/view?usp=sharing). This is originally from a [Kaggle dataset](https://www.kaggle.com/competitions/ieee-fraud-detection/data) for Fraud Detection. Place this dataset in a `data` directory in the root of your project.

## Istio Ingress
First, we need to setup Istio for ingress. Seldon Core will utilize Istio to ensure that our traffic is rooted to the appropriate pods through the same endpoint. This process is pretty simple. First let's start Minikube and switch to that context.
```base
minikube start
kubectl config use-context minikube
```

Then we need to add the Istio repo install the core charts.
```bash
# Add Istio Repo
helm repo add istio https://istio-release.storage.googleapis.com/charts
helm repo update

# Create Namespace
kubectl create namespace istio-system

# Install base chart
helm install istio-base istio/base -n istio-system

# Install discovery chart
helm install istiod istio/istiod -n istio-system --wait
```

Now, let's add the ingress controller.
```bash
# Install ingress chart
kubectl create namespace istio-ingress
kubectl label namespace istio-ingress istio-injection=enabled
helm install istio-ingress istio/gateway -n istio-ingress --wait
```

Finally, add a gateway!
```bash
kubectl apply -f - << END
apiVersion: networking.istio.io/v1alpha3
kind: Gateway
metadata:
  name: seldon-gateway
  namespace: istio-system
spec:
  selector:
    istio: ingressgateway # use istio default controller
  servers:
  - port:
      number: 80
      name: http
      protocol: HTTP
    hosts:
    - "*"
END
```

Easy peasy! Now we have an ingress controller that will route traffic to our models.

## Seldon Core Setup
Seldon Core is a service that will create ML deployments on our K8s cluster and provide the ability to conduct A/B testing and canary deployments, as well as monitor the performance of our models in a dashboard.

<!-- 
There are 2 modules we need to install, the analytics component and the core component. We'll install the analytics chart first. This chart will install [Prometheus](http://www.prometheusanalytics.net) under the hood for pod resource monitoring with a Grafana based dashboard.

```bash
helm upgrade --install seldon-core-analytics seldon-core-analytics \
    --repo https://storage.googleapis.com/seldon-charts \
    --set grafana.adminPassword="admin" \
    --create-namespace \
    --namespace seldon-system
```

ALT: We are going to install prometheus.
```bash
kubectl create namespace seldon-monitoring

helm upgrade --install seldon-monitoring kube-prometheus \
    --version 6.9.5 \
    --set fullnameOverride=seldon-monitoring \
    --namespace seldon-monitoring \
    --repo https://charts.bitnami.com/bitnami

kubectl rollout status -n seldon-monitoring statefulsets/prometheus-seldon-monitoring-prometheus
```
--->

Using helm, let's install the core chart. This installation will allow us to create special ML deployments and contains the bulk of Seldon's functionality.

```bash
helm install seldon-core seldon-core-operator \
    --repo https://storage.googleapis.com/seldon-charts \
    --set usageMetrics.enabled=true \
    --set istio.enabled=true \
    --namespace seldon-system
```

We're gonna need some images to deploy. We have supplied a training file `train.py` that will train 2 models and save them to your BentoML store. There is also a `bentofile.yaml` which we can use to build the two Bentos into a service. You will need to run `bento build` twice, modifying the `fraud_detection_service.py` file to use the correct model. You can do so by changing the `model_type` variable in `train.py`. Note the tag of the Bento.

```python
#### ... in fraud_detection_service.py
model_type = "xgb"
# model_type = "rf"
```
```bash
bento build
# Change model_type to rf
bento build
```

Let's containerize them now using the correct Bento tags and tag them with their respective model names. Before we build, let's point our shell to minikube registry.
```bash
eval $(minikube docker-env)
bentoml containerize fraud_classifier:<xgb-tag> -t fraud-classifier:xgb
bentoml containerize fraud_classifier:<rf-tag> -t fraud-classifier:rf
```

## Deployment and running Canaries
Canary deployments are a way to monitor the performance of your model versus some baseline. They work by deploying two or more versions of your model, routing some traffic to the baseline and the rest to the other. This is a great way to monitor the performance of new models you develop.

To deploy, we're going to create a special kind of Kubernetes deployment called a **SeldonDeployment**. This specific resource allows us to add a `predictors` field to our spec. This field is a list of predictor objects, which are used to specify the ML models to use for a given request, how the traffic should be divided between the two models and any necessary preprocessing to be done (though we will not tackle this specifically in this tutorial). Create a file called `deployment_xgb.yaml` and add the following to it:
```yaml
apiVersion: machinelearning.seldon.io/v1alpha2
kind: SeldonDeployment
metadata:
  labels:
    app: seldon
  name: fraud-detection
  namespace: default

```
These are usual fields that you fill out for a Kubernetes deployment. Note the kind of deployment is `SeldonDeployment`.

Now let's add the spec block. This is where we specify the ML models to use for a given request. This is done under the `predictors` field, which contains a list of different model-based services. Within each of these services, we specify the docker image to use under `componentSpecs.spec.containers`, the name of the model under `name`, the traffic split under `traffic` and the number of pod replicas under `replicas`. There is also a `graph.children` field that we can use to feed output from the root predictor into additional predictors as a processing pipeline, though we will not be using this functionality in this tutorial. We are going to deploy our XGBoost model.

```yaml
spec:
  name: fraud-detection
  annotations:
    project_name: fraud_detection_service
    deployment_version: v1
  predictors:
    - name: xgb
      replicas: 1
      traffic: 100
      componentSpecs:
        - spec:
            containers:
              - image: fraud-classifier:xgb
                imagePullPolicy: IfNotPresent
                name: xgb
                env:
                  - name: VERSION
                    value: "XGBoost"
            terminationGracePeriodSeconds: 1
      graph:
        children: []
        endpoint:
          type: REST
        name: xgb
        type: MODEL
```
We can deploy this with `kubectl`.
  
```bash
kubectl apply -f deployment_xgb.yaml
```


Great, we have created our first deployment with Seldon Core. Now, let's add a canary deployment. Duplicate your `deployment_xgb.yaml` file and rename it `deployment_canary.yaml`. In the new file, rename the `name` field to `fraud_detection_canary`.

```yaml
metadata:
  labels:
    app: seldon
  name: fraud-detection-canary
...
spec:
  name: fraud_detection-canary
...
```

Now we can add our Random Forest model. Do this under the `predictors` field.

```yaml
predictors:
...
  - name: rf
    replicas: 1
    traffic: 50
    componentSpecs:
      - spec:
          containers:
            - image: fraud-classifier:rf
              imagePullPolicy: IfNotPresent
              name: rf
              env:
                - name: VERSION
                  value: "RandomForest"
          terminationGracePeriodSeconds: 1
    graph:
      children: []
      endpoint:
        type: REST
      name: rf
      type: MODEL

```
<!-- ## Analytics
## Epsilon Greedy Dep 
## Multi-Armed Bandit
## Shadow Deployment -->