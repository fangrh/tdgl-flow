FLUX_OWNER  := fangrh
FLUX_REPO   := tdgl-flow
FLUX_BRANCH := main
FLUX_PATH   := clusters/k3s
NAMESPACE   := tdgl

.PHONY: install-flux check bootstrap apply verify status port-forward reconcile suspend resume clean

install-flux:
	curl -s https://fluxcd.io/install.sh | sudo bash
	flux --version

check:
	flux check --pre

bootstrap:
ifndef GITHUB_TOKEN
	$(error GITHUB_TOKEN is required. Run: export GITHUB_TOKEN=ghp_your_token)
endif
	flux bootstrap github \
		--owner=$(FLUX_OWNER) \
		--repository=$(FLUX_REPO) \
		--branch=$(FLUX_BRANCH) \
		--path=$(FLUX_PATH) \
		--personal

apply:
	kubectl apply -f clusters/k3s/tdgl-resources.yaml

verify:
	flux get kustomizations
	flux get sources git

status:
	kubectl get pods -n $(NAMESPACE)

port-forward:
	kubectl port-forward -n istio-system svc/istio-ingressgateway 80:80 --address 0.0.0.0

reconcile:
	flux reconcile kustomization tdgl-infra --with-source
	flux reconcile kustomization tdgl-services --with-source

suspend:
	flux suspend kustomization tdgl-services
	flux suspend kustomization tdgl-infra

resume:
	flux resume kustomization tdgl-infra
	flux resume kustomization tdgl-services

clean:
	flux uninstall
