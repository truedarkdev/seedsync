# Copyright 2017, Inderpreet Singh, All rights reserved.

# Catch sigterms
# See: https://stackoverflow.com/a/52159940
export SHELL:=/bin/bash
export SHELLOPTS:=$(if $(SHELLOPTS),$(SHELLOPTS):)pipefail:errexit
.ONESHELL:

# Color outputs
red=`tput setaf 1`
green=`tput setaf 2`
reset=`tput sgr0`

ROOTDIR:=$(shell realpath .)
SOURCEDIR:=$(shell realpath ./src)
BUILDDIR:=$(shell realpath ./build)
PYTEST_ARTIFACT_DIR:=$(ROOTDIR)/tmp/pytest
DEFAULT_STAGING_REGISTRY:=localhost:5000
DOCKER_IMAGE_PLATFORMS:=linux/amd64,linux/arm64,linux/arm/v7
DEB_GLIBC_MAX:=2.31
SCANFS_PLATFORM?=linux/amd64
SCANFS_PLATFORM_SUPPORTED:=linux/amd64 linux/arm64 linux/arm/v7
# Optional prebuilt Angular output can shadow the in-Docker Angular build stage.
SEEDSYNC_ANGULAR_BUILD_CONTEXT_ARG:=$(if $(SEEDSYNC_ANGULAR_BUILD_CONTEXT),--build-context seedsync_build_angular=$(SEEDSYNC_ANGULAR_BUILD_CONTEXT),)

ifeq ($(value SCANFS_PLATFORM),linux/amd64)
else ifeq ($(value SCANFS_PLATFORM),linux/arm64)
else ifeq ($(value SCANFS_PLATFORM),linux/arm/v7)
else
$(error Unsupported SCANFS_PLATFORM '$(value SCANFS_PLATFORM)'; supported values: $(SCANFS_PLATFORM_SUPPORTED))
endif

#DOCKER_BUILDKIT_FLAGS=BUILDKIT_PROGRESS=plain
DOCKER=${DOCKER_BUILDKIT_FLAGS} DOCKER_BUILDKIT=1 docker
DOCKER_COMPOSE=${DOCKER_BUILDKIT_FLAGS} COMPOSE_DOCKER_CLI_BUILD=1 DOCKER_BUILDKIT=1 docker compose

.PHONY: builddir deb docker-image test-image tests-python run-tests-python run-tests-python-native run-tests-python-wsl verify-deb-glibc verify-scanfs-glibc preflight-linux-wsl upgrade-v086-preflight upgrade-v086-build upgrade-v086-start upgrade-v086-status upgrade-v086-restart upgrade-v086-build-transient upgrade-v086-start-transient upgrade-v086-transient upgrade-v086-stop clean coverage-python check-python-tooling lint-python typecheck-python

all: deb docker-image

builddir:
	mkdir -p ${BUILDDIR}

scanfs: builddir
	$(DOCKER) build \
		-f ${SOURCEDIR}/docker/build/deb/Dockerfile \
		--build-arg SCANFS_PLATFORM=${SCANFS_PLATFORM} \
		--target seedsync_build_scanfs_export \
		--output ${BUILDDIR} \
		${ROOTDIR}

deb: builddir
	$(DOCKER) build \
		-f ${SOURCEDIR}/docker/build/deb/Dockerfile \
		--build-arg SCANFS_PLATFORM=${SCANFS_PLATFORM} \
		--target seedsync_build_deb_export \
		--output ${BUILDDIR} \
		${ROOTDIR}

docker-buildx:
	$(DOCKER) run --rm --privileged multiarch/qemu-user-static --reset -p yes

docker-image: docker-buildx
	@if [[ -z "${STAGING_REGISTRY}" ]] ; then \
		export STAGING_REGISTRY="${DEFAULT_STAGING_REGISTRY}"; \
	fi;
	echo "${green}STAGING_REGISTRY=$${STAGING_REGISTRY}${reset}";
	@if [[ -z "${STAGING_VERSION}" ]] ; then \
		export STAGING_VERSION="latest"; \
	fi;
	echo "${green}STAGING_VERSION=$${STAGING_VERSION}${reset}";

	# final image
	$(DOCKER) buildx build \
		-f ${SOURCEDIR}/docker/build/docker-image/Dockerfile \
		--build-arg SCANFS_PLATFORM=${SCANFS_PLATFORM} \
		$(SEEDSYNC_ANGULAR_BUILD_CONTEXT_ARG) \
		--target seedsync_run \
		--tag $${STAGING_REGISTRY}/seedsync:$${STAGING_VERSION} \
		--cache-to=type=registry,ref=$${STAGING_REGISTRY}/seedsync:cache,mode=max \
		--cache-from=type=registry,ref=$${STAGING_REGISTRY}/seedsync:cache \
		--platform ${DOCKER_IMAGE_PLATFORMS} \
		--push \
		${ROOTDIR}

docker-image-release:
	@if [[ -z "${STAGING_REGISTRY}" ]] ; then \
		export STAGING_REGISTRY="${DEFAULT_STAGING_REGISTRY}"; \
	fi;
	echo "${green}STAGING_REGISTRY=$${STAGING_REGISTRY}${reset}";
	@if [[ -z "${RELEASE_REGISTRY}" ]] ; then \
		echo "${red}ERROR: RELEASE_REGISTRY is required${reset}"; exit 1; \
	fi
	@if [[ -z "${RELEASE_VERSION}" ]] ; then \
		echo "${red}ERROR: RELEASE_VERSION is required${reset}"; exit 1; \
	fi
	echo "${green}RELEASE_REGISTRY=${RELEASE_REGISTRY}${reset}"
	echo "${green}RELEASE_VERSION=${RELEASE_VERSION}${reset}"

	# final image
	$(DOCKER) buildx build \
		-f ${SOURCEDIR}/docker/build/docker-image/Dockerfile \
		--build-arg SCANFS_PLATFORM=${SCANFS_PLATFORM} \
		$(SEEDSYNC_ANGULAR_BUILD_CONTEXT_ARG) \
		--target seedsync_run \
		--tag ${RELEASE_REGISTRY}/seedsync:${RELEASE_VERSION} \
		--cache-from=type=registry,ref=$${STAGING_REGISTRY}/seedsync:cache \
		--platform ${DOCKER_IMAGE_PLATFORMS} \
		--push \
		${ROOTDIR}

verify-deb-glibc:
	@if ! compgen -G "${BUILDDIR}/*.deb" > /dev/null; then \
		echo "${red}ERROR: No .deb artifact found in ${BUILDDIR}${reset}"; exit 1; \
	fi
	@deb_files=( ${BUILDDIR}/*.deb ); \
	if [[ $${#deb_files[@]} -ne 1 ]]; then \
		echo "${red}ERROR: Expected exactly one .deb artifact in ${BUILDDIR}, found $${#deb_files[@]}${reset}"; exit 1; \
	fi; \
	${SOURCEDIR}/docker/test/verify_glibc.sh "$${deb_files[0]}" ${DEB_GLIBC_MAX}

verify-scanfs-glibc:
	@if [[ ! -f "${BUILDDIR}/scanfs" ]]; then \
		echo "${red}ERROR: scanfs artifact not found at ${BUILDDIR}/scanfs${reset}"; exit 1; \
	fi
	${SOURCEDIR}/docker/test/verify_glibc.sh "${BUILDDIR}/scanfs" ${DEB_GLIBC_MAX}

preflight-linux-wsl:
	bash ${SOURCEDIR}/docker/test/check_linux_wsl_baseline.sh

upgrade-v086-preflight:
	bash ${SOURCEDIR}/docker/test/upgrade-v086/lab.sh preflight

upgrade-v086-build:
	bash ${SOURCEDIR}/docker/test/upgrade-v086/lab.sh build

upgrade-v086-start:
	bash ${SOURCEDIR}/docker/test/upgrade-v086/lab.sh start

upgrade-v086-build-transient:
	bash ${SOURCEDIR}/docker/test/upgrade-v086/lab.sh build-transient

upgrade-v086-start-transient:
	bash ${SOURCEDIR}/docker/test/upgrade-v086/lab.sh start transient

upgrade-v086-status:
	bash ${SOURCEDIR}/docker/test/upgrade-v086/lab.sh status

upgrade-v086-restart:
	bash ${SOURCEDIR}/docker/test/upgrade-v086/lab.sh restart

upgrade-v086-transient:
	bash ${SOURCEDIR}/docker/test/upgrade-v086/lab.sh transient

upgrade-v086-stop:
	bash ${SOURCEDIR}/docker/test/upgrade-v086/lab.sh stop

test-image:
	# python run
	$(DOCKER) build \
		-f ${SOURCEDIR}/docker/build/docker-image/Dockerfile \
		--target seedsync_run_python_devenv \
		--tag seedsync/run/python/devenv \
		${ROOTDIR}
	# python tests
	$(DOCKER) build \
		-f ${SOURCEDIR}/docker/test/python/Dockerfile \
		--target seedsync_test_python \
		--tag seedsync/test/python \
		${ROOTDIR}

tests-python: test-image

run-tests-python: test-image
	$(DOCKER_COMPOSE) \
		-f ${SOURCEDIR}/docker/test/python/compose.yml \
		up --force-recreate --no-build --exit-code-from tests

run-tests-python-native:
	# native host python tests
	mkdir -p ${PYTEST_ARTIFACT_DIR}
	cd ${SOURCEDIR}/python && poetry run pytest -p no:cacheprovider

run-tests-python-wsl:
	# WSL/Linux live SSH + archive lane; pass EXTRA_ARGS=--preflight-only for a smoke check.
	bash ${SOURCEDIR}/docker/test/python/run_wsl_lane.sh --live-ssh ${EXTRA_ARGS}

# Local Python lint/typecheck lane. Poetry-managed dependency refresh stays deferred on this host.
check-python-tooling: lint-python typecheck-python

lint-python:
	python_bin="$$(command -v python3 || command -v python)"; \
	if [[ -z "$${python_bin}" ]]; then \
		echo "${red}ERROR: python or python3 is required for the Ruff lane${reset}"; exit 1; \
	fi; \
	if ! "$${python_bin}" -m ruff --version >/dev/null 2>&1; then \
		"$${python_bin}" -m pip install --user --upgrade ruff==0.15.18; \
	fi; \
	cd ${SOURCEDIR}/python && "$${python_bin}" -m ruff check .

typecheck-python:
	cd ${SOURCEDIR}/python && npx --yes pyright@1.1.410 --project pyrightconfig.json

tests-angular:
	# angular build
	$(DOCKER) build \
		-f ${SOURCEDIR}/docker/build/deb/Dockerfile \
		--target seedsync_build_angular_env \
		--tag seedsync/build/angular/env \
		${ROOTDIR}
	# angular tests
	$(DOCKER_COMPOSE) \
		-f ${SOURCEDIR}/docker/test/angular/compose.yml \
		build

run-tests-angular: tests-angular
	$(DOCKER_COMPOSE) \
		-f ${SOURCEDIR}/docker/test/angular/compose.yml \
		up --force-recreate --exit-code-from tests

tests-e2e-deps:
	# deb pre-reqs
	$(DOCKER) build \
		${SOURCEDIR}/docker/stage/deb/ubuntu-systemd/ubuntu-20.04-systemd \
		-t ubuntu-systemd:20.04

	# Setup docker for the systemd container
	$(DOCKER) run --rm --privileged -v /:/host ubuntu-systemd:20.04 setup

run-tests-e2e: tests-e2e-deps
	# Check our settings
	@if [[ -z "${STAGING_VERSION}" ]] && [[ -z "${SEEDSYNC_DEB}" ]]; then \
		echo "${red}ERROR: One of STAGING_VERSION or SEEDSYNC_DEB must be set${reset}"; exit 1; \
	elif [[ ! -z "${STAGING_VERSION}" ]] && [[ ! -z "${SEEDSYNC_DEB}" ]]; then \
	  	echo "${red}ERROR: Only one of STAGING_VERSION or SEEDSYNC_DEB must be set${reset}"; exit 1; \
  	fi

	# Set up environment for deb
	@if [[ ! -z "${SEEDSYNC_DEB}" ]] ; then \
		if [[ -z "${SEEDSYNC_OS}" ]] ; then \
			echo "${red}ERROR: SEEDSYNC_OS is required for DEB e2e test${reset}"; \
			echo "${red}Use SEEDSYNC_OS=ubu2004 for the active DEB e2e lane (Ubuntu 20.04)${reset}"; exit 1; \
		elif [[ "${SEEDSYNC_OS}" != "ubu2004" ]] ; then \
			echo "${red}ERROR: Active DEB e2e policy requires SEEDSYNC_OS=ubu2004 (Ubuntu 20.04)${reset}"; exit 1; \
		fi
	fi

	# Set up environment for image
	@if [[ ! -z "${STAGING_VERSION}" ]] ; then \
		if [[ -z "${SEEDSYNC_ARCH}" ]] ; then \
			echo "${red}ERROR: SEEDSYNC_ARCH is required for docker image e2e test${reset}"; \
			echo "${red}Options include: amd64, arm64, arm/v7${reset}"; exit 1; \
		fi
		RESOLVED_SEEDSYNC_PLATFORM=`${SOURCEDIR}/docker/test/resolve_platform.sh "$${SEEDSYNC_ARCH}"`; \
		if [[ ! -z "${SEEDSYNC_PLATFORM}" ]] && [[ "${SEEDSYNC_PLATFORM}" != "$${RESOLVED_SEEDSYNC_PLATFORM}" ]] ; then \
			echo "${red}ERROR: SEEDSYNC_PLATFORM=${SEEDSYNC_PLATFORM} does not match SEEDSYNC_ARCH=${SEEDSYNC_ARCH}${reset}"; exit 1; \
		fi; \
		export SEEDSYNC_PLATFORM="$${RESOLVED_SEEDSYNC_PLATFORM}"; \
		echo "${green}SEEDSYNC_PLATFORM=$${SEEDSYNC_PLATFORM}${reset}"; \
		if [[ -z "${STAGING_REGISTRY}" ]] ; then \
			export STAGING_REGISTRY="${DEFAULT_STAGING_REGISTRY}"; \
		fi;
		echo "${green}STAGING_REGISTRY=$${STAGING_REGISTRY}${reset}";
		# Removing and pulling is the only way to select the arch from a multi-arch image :(
		$(DOCKER) rmi -f $${STAGING_REGISTRY}/seedsync:$${STAGING_VERSION} || true
		$(DOCKER) pull $${STAGING_REGISTRY}/seedsync:$${STAGING_VERSION} --platform $${SEEDSYNC_PLATFORM}
	fi

	# Set the flags
	COMPOSE_FLAGS="-f ${SOURCEDIR}/docker/test/e2e/compose.yml "
	COMPOSE_RUN_FLAGS=""
	if [[ ! -z "${SEEDSYNC_DEB}" ]] ; then
		COMPOSE_FLAGS+="-f ${SOURCEDIR}/docker/stage/deb/compose.yml "
		COMPOSE_FLAGS+="-f ${SOURCEDIR}/docker/stage/deb/compose-${SEEDSYNC_OS}.yml "
	fi
	if [[ ! -z "${STAGING_VERSION}" ]] ; then \
		COMPOSE_FLAGS+="-f ${SOURCEDIR}/docker/stage/docker-image/compose.yml "
	fi
	if [[ "${DEV}" = "1" ]] ; then
		COMPOSE_FLAGS+="-f ${SOURCEDIR}/docker/test/e2e/compose-dev.yml "
	else \
  		COMPOSE_RUN_FLAGS+="-d"
	fi
	echo "${green}COMPOSE_FLAGS=$${COMPOSE_FLAGS}${reset}"

	# Set up Ctrl-C handler
	function tearDown {
		$(DOCKER_COMPOSE) \
			$${COMPOSE_FLAGS} \
			stop
	}
	trap tearDown EXIT

	# Build the test
	echo "${green}Building the tests${reset}"
	$(DOCKER_COMPOSE) \
		$${COMPOSE_FLAGS} \
		build

	# This suppresses the docker-compose error that image has changed
	$(DOCKER_COMPOSE) \
		$${COMPOSE_FLAGS} \
		rm -f myapp

	# Run the test
	echo "${green}Running the tests${reset}"
	$(DOCKER_COMPOSE) \
		$${COMPOSE_FLAGS} \
		up --force-recreate \
		$${COMPOSE_RUN_FLAGS}

	if [[ "${DEV}" != "1" ]] ; then
		$(DOCKER) logs -f seedsync_test_e2e
	fi

	EXITCODE=`$(DOCKER) inspect seedsync_test_e2e | jq '.[].State.ExitCode'`
	if [[ "$${EXITCODE}" != "0" ]] ; then
		false
	fi

run-remote-server:
	STAGING_REGISTRY="$(if $(strip ${STAGING_REGISTRY}),${STAGING_REGISTRY},localhost:5000)" \
	STAGING_VERSION="$(if $(strip ${STAGING_VERSION}),${STAGING_VERSION},latest)" \
	SEEDSYNC_REMOTE_FILES_DIR="$(if $(strip ${SEEDSYNC_REMOTE_FILES_DIR}),${SEEDSYNC_REMOTE_FILES_DIR},${ROOTDIR}/build/docker-local/remote-files)" \
		$(DOCKER_COMPOSE) \
		-f ${SOURCEDIR}/docker/test/e2e/compose.yml \
		-f ${SOURCEDIR}/docker/stage/docker-image/compose.yml \
		-f ${SOURCEDIR}/docker/test/e2e/compose-remote-dev.yml \
		up -d --build remote

stop-remote-server:
	STAGING_REGISTRY="$(if $(strip ${STAGING_REGISTRY}),${STAGING_REGISTRY},localhost:5000)" \
	STAGING_VERSION="$(if $(strip ${STAGING_VERSION}),${STAGING_VERSION},latest)" \
	SEEDSYNC_REMOTE_FILES_DIR="$(if $(strip ${SEEDSYNC_REMOTE_FILES_DIR}),${SEEDSYNC_REMOTE_FILES_DIR},${ROOTDIR}/build/docker-local/remote-files)" \
		$(DOCKER_COMPOSE) \
		-f ${SOURCEDIR}/docker/test/e2e/compose.yml \
		-f ${SOURCEDIR}/docker/stage/docker-image/compose.yml \
		-f ${SOURCEDIR}/docker/test/e2e/compose-remote-dev.yml \
		stop remote

coverage-python:
	cd ${SOURCEDIR}/python && poetry run pytest --cov --cov-report=term-missing --cov-report=html

clean:
	rm -rf ${BUILDDIR}
