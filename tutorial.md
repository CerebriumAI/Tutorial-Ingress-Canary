# Deployment 2.0: Canaries with KServe
In this tutorial, we will build on our previous deployment tutorial and augment our cluster an ingress and add the ability to conduct canary deployments.

When we deploy ML models, we are often unsure of the performance of our model in the real world. Methods like A/B testing and canary deployments allow us to evaluate the performance of our model and avoid erroneous deployments where the model is not performing as expected. This is done by splitting traffic between two ML services, typically your *baseline* model and a new model.

We are going to use two core technologies to implement canary deployments, [Istio](https://istio.io) and [KServe](https://kserve.github.io/website/0.9/). Briefly, Istio is a service mesh that will supply an ingress controller, while KServe is a service that will create deployments on our K8s cluster serve our models through Istio. It is worth noting that you could use [Ambassador](https://www.getambassador.io) as your ingress controller instead of Istio, but we ran into issues with Ambassador so use at your discretion!

Prerequisites:
- Install Minikube
- Install BentoML
- Previous Deployment Tut

By the end of this tutorial you will be able to:
- Setup Istio for ingress
- Setup Seldon Core
- Run a Canary Deployment with KServe
  
This tutorial assumes you have done the [previous tutorial](https://hippocampus.podia.com/view/courses/build-an-end-to-end-production-grade-fraud-predictor/1462864-deploying-with-bentoml-on-kubernetes) on [BentoML](https://www.bentoml.com) and Kubernetes. We will be using [minikube](https://minikube.sigs.k8s.io/docs/start/) again. If you wish to use a managed cloud cluster go for it, though you will need to pay for additional resources for Istio (it requires at least 4GB RAM)! You should have it already if you have done previous tuts, but you can download the data required for this tutorial from [here](https://drive.google.com/file/d/1MidRYkLdAV-i0qytvsflIcKitK4atiAd/view?usp=sharing). This is originally from a [Kaggle dataset](https://www.kaggle.com/competitions/ieee-fraud-detection/data) for Fraud Detection. Place this dataset in a `data` directory in the root of your project.

## Istio Ingress Setup
Firstly, we need to setup Istio for ingress. KServe will utilize Istio to ensure that our traffic is rooted to the appropriate pods through the same endpoint. This process is pretty simple. First let's start minikube and switch to that context. You may need to go through specific platform setup if you don't want to use minikube, so consult the relevant guide [here](https://istio.io/latest/docs/setup/platform-setup/). We provide 2 install methods, though you should stick with the first if you don't use [helm](https://helm.sh).

For this tut, minikube we will need additional resources for Istio. Start minikube with at least 4GB of RAM and 2 vCPUs. In this command we use 8GB of RAM and 4 vCPUs. You may need to delete your previously created cluster or create a new one under a new context.
```base
minikube start --memory 8192 --cpus 4
kubectl config use-context minikube
```
### Main Install: `istioctl` install
The recommended way to setup Istio is to use the [istioctl](https://istio.io/latest/docs/setup/quick-start.html) command line tool.
We will use the following command to install for macOS, but obviously use the relevant install method for your platform. You can read the docs [here](https://istio.io/latest/docs/setup/install/istioctl/#prerequisites).

```bash
brew install istioctl
```

Installation should now be as simple as running the install command.
```bash
istioctl install
```

### Alternative Install: Helm
If you are accustomed to using Helm, you can use the following commands to install Istio. Note, this is in **alpha**, so you may encounter issues.

Add the Istio repo and install the core charts.
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
  name: kserve-gateway
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

## KServe Setup
KServe is a service that will create ML deployments on our K8s cluster and provide the ability to conduct canary deployments. Like BentoML, KServe is model and framework agnostic. While we use Bento service containers here, with KServe you can use raw model files and simply point to the URI where the model is located if that suits your use case better. Alternatively, there are a couple of other services we can use to manage our ML deployments. Check them out if you are interested.
- [Cortex](https://www.cortex.dev) - Cortex is a tool that allows you to manage your ML deployments easily via CLI. It is AWS only, but it is a great choice if you are using the AWS EKS stack.
- [Seldon Core](https://www.seldon.io/solutions/open-source-projects/core) - Very similar to KServe, Seldon Core is a Kubernetes-only service that allows you to manage your ML deployments. Seldon Core is slightly more feature rich, but is a more complex tool to setup and use. However, compatibility with BentoML 1.0 in its current state is limited, as the framework is built as the backbone for their proprietary platform Seldon Deploy.

[cert-manager](https://cert-manager.io/) is a service that allows us to easily manage TLS certificates. It is a required dependency for KServe. Install it before proceeding.
```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.9.1/cert-manager.yaml
```

Using `kubectl`, let's install the core manifest. 
```bash
kubectl apply -f https://github.com/kserve/kserve/releases/download/v0.9.0/kserve.yaml

```

There are also default serving runtimes which are necessary for KServe to function.
```bash
kubectl apply -f https://github.com/kserve/kserve/releases/download/v0.9.0/kserve-runtimes.yaml
```

Lastly, you'll need to modify the default deployment mode and ingress.
```bash
kubectl patch configmap/inferenceservice-config -n kserve --type=strategic -p '{"data": {"deploy": "{\"defaultDeploymentMode\": \"RawDeployment\"}"}}'
```

<!-- From now on, in your deployments, you will need to modify `ingressClassName` in the `ingress` field to the `IngressClass` name created in the previous step.
```yaml
ingress: |-
{
    "ingressClassName" : "your-ingress-class",
}
``` -->

## Creating our Bentos

We're gonna need some images to deploy. We have supplied a training file `train.py` that will train 2 models and save them to your BentoML store.
```bash
python train.py
```

There is also a `bentofile.yaml` which we can use to build the two Bentos into a service. You will need to run `bento build` twice, modifying the `fraud_detection_service.py` file to use the correct model. You can do so by changing the `model_type` variable in `train.py`. Note the tags of each Bento.

```python
#### ... in fraud_detection_service.py
model_type = "xgb"
# model_type = "rf" # Uncomment this
```
```bash
bento build
# Change model_type to rf after first build
bento build
```

Let's containerize them now using the correct Bento tags and tag them with their respective model names. Before we build, let's point our shell to the minikube registry.
```bash
eval $(minikube docker-env)
bentoml containerize fraud_classifier:<xgb-tag> -t fraud-classifier:xgb
bentoml containerize fraud_classifier:<rf-tag> -t fraud-classifier:rf
```

## Deployment with KServe
Before we deploy a KServe manifest, let's go through the benefits of using KServe. Apart from allowing us to conduct Canary deployments, KServe gives us a number of tools to use to manage our deployments. Whether you need such functionality will largely depend on how mature your organization is and how much scalability you require with regards to machine learning services. We do recommended running Canaries at all levels of scale apart from your initial rollout, which is our main motivation behind this tut, but here are some of the other things KServe offers:
- *Serverless Inference* - This feature enables autoscaling based on request volume, and allows KServe to scale down a service to and from zero resources. This sort of install is useful if you are handling many requests. In most cases, this is likely unnecessary, but you can read more about the installation [here](https://kserve.github.io/website/0.9/admin/serverless/).
- *ModelMesh* - In cases where you frequently need to change which model to use for a given situation, ModelMesh is a great tool to use. The system will switch between models automatically without having to redeploy, ensuring you will use the best model for the current available computation to maximize responsiveness to users.
- *Pre/Post Processing Inference Graph* -  KServe allows you to specify an Inference Graph to build inference pipelines. Within the graph, you can define pre and post processing steps, traffic splits, model ensembles and model switching based on defined conditions. You can read more about how this works [here](https://kserve.github.io/website/0.9/modelserving/inference_graph/).
- *Model Monitoring & Explainability* - KServe has built-in integration with both [Alibi Detect](https://kserve.github.io/website/0.9/modelserving/detect/alibi_detect/alibi_detect/) and [Alibi Explain](https://kserve.github.io/website/0.9/modelserving/explainer/explainer/). This enables outlier and drift detection easily, as well as a black-box model for explainability. We will cover these specific services in a future version of this tutorial.

To initially deploy, we're going to create a special kind of Kubernetes deployment called a **InferenceService**. This specific resource allows us to add a `predictor` field to our manifest. This field is a spec used to specify the ML model to use for a given request and how much traffic should be routed to the service. 

Create a file called `deployment_xgb.yaml` and add the following to it:
```yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  labels:
    app: kserve
  name: fraud-detection
  namespace: kserve-deployments
...
```
These are usual fields that you fill out for a Kubernetes deployment. Note the kind of deployment is `InferenceService`.

Now let's add the spec block. This is where we specify the ML models to use for a given request. This is done under the `predictor` field. Within this spec, we specify the docker image to use under `containers`. KServe allows you to serve specific model files too. For example, instead of `containers` we could instead specify a `sklearn` field to serve a pickled sklearn model instead (You can see an example [here](https://kserve.github.io/website/get_started/first_isvc/)).

```yaml
...
spec:
  predictor:
    containers:
      - image: fraud-classifier:xgb
        imagePullPolicy: IfNotPresent
        name: xgb
        env:
          - name: VERSION
            value: "XGBoost"
        ports:
        - containerPort: 3000
        securityContext:
          runAsUser: 1034
```

We should create a separate namespace for our deployment. We've named it `kserve-deployments`, but name it whatever you like.
```bash
kubectl create namespace kserve-deployments
```

We can deploy now deploy our manifest with `kubectl`.
```bash
kubectl apply -f deployment_xgb.yaml
```

Great, we have created our first deployment with KServe! Check that it's running correctly with minikube.
```bash
minikube service fraud-detection-predictor-default -n kserve-deployments
```

## Canary Deployment
Canary deployments are a way to progressively rollout your new models to a subset of users, monitoring the performance of your newly deployed model versus the original baseline. They work by deploying two or more versions of your model, routing some traffic to the baseline and the rest to the other. As you become more confident in the new model, you can increase traffic to the new service and eventually switch to it entirely. This is a great way to ensure the new model is performing as expected, while also monitoring the service for any potential errors.

Let's add a canary deployment. Duplicate your `deployment_xgb.yaml` file and rename it `deployment_canary.yaml`. In the new file, rename the `name` field to `fraud_detection_canary`.

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
