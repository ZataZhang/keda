#!/usr/bin/env bash
set -euo pipefail

gitea_runtime_directory="/private/tmp/issue416-gitea"
gitea_container_name="issue416-gitea"
gitea_base_url="http://127.0.0.1:3300"
gitea_admin_username="rvadmin"
gitea_repository_name="keda-rv"

mkdir -p "$gitea_runtime_directory"
chmod 700 "$gitea_runtime_directory"
if docker inspect "$gitea_container_name" >/dev/null 2>&1; then
  docker rm -f "$gitea_container_name" >/dev/null
fi
# 默认把 3000 端口发布到宿主 127.0.0.1:3300；在容器内复跑时设置
# RV_GITEA_NETWORK_MODE=container:<复跑容器>，让 Gitea 直接监听共享网络命名空间的 3300。
if [[ -n "${RV_GITEA_NETWORK_MODE:-}" ]]; then
  gitea_network_arguments=(--network "$RV_GITEA_NETWORK_MODE" --env GITEA__server__HTTP_PORT=3300)
else
  gitea_network_arguments=(--publish 127.0.0.1:3300:3000)
fi
docker run --detach \
  --name "$gitea_container_name" \
  "${gitea_network_arguments[@]}" \
  --env GITEA__security__INSTALL_LOCK=true \
  --env GITEA__database__DB_TYPE=sqlite3 \
  --env GITEA__server__ROOT_URL="$gitea_base_url/" \
  --env GITEA__service__DISABLE_REGISTRATION=true \
  gitea/gitea:1.21 >/dev/null

for startup_attempt in $(seq 1 90); do
  if curl --silent --fail "$gitea_base_url/api/v1/version" >/dev/null; then
    break
  fi
  if [[ "$startup_attempt" == 90 ]]; then
    docker logs "$gitea_container_name" >&2
    exit 1
  fi
  sleep 1
done

gitea_admin_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(30))')"
docker exec --user git "$gitea_container_name" gitea admin user create \
  --username "$gitea_admin_username" \
  --password "$gitea_admin_password" \
  --email rvadmin@example.invalid \
  --admin \
  --must-change-password=false >/dev/null

gitea_access_token="$(docker exec --user git "$gitea_container_name" gitea admin user generate-access-token \
  --username "$gitea_admin_username" \
  --token-name issue416-validation \
  --scopes all \
  --raw)"
gitea_repository_payload="$(printf '{"name":"%s","private":false,"auto_init":true,"default_branch":"main"}' "$gitea_repository_name")"
curl --silent --show-error --fail \
  --request POST \
  --header "Authorization: token $gitea_access_token" \
  --header 'Content-Type: application/json' \
  --data "$gitea_repository_payload" \
  "$gitea_base_url/api/v1/user/repos" >/dev/null

gitea_runtime_environment="$gitea_runtime_directory/runtime.env"
umask 077
cat >"$gitea_runtime_environment" <<EOF
export RV_GITEA_BASE_URL='$gitea_base_url'
export RV_GITEA_USERNAME='$gitea_admin_username'
export RV_GITEA_TOKEN='$gitea_access_token'
export RV_GITEA_REPOSITORY='$gitea_repository_name'
EOF
chmod 600 "$gitea_runtime_environment"
unset gitea_admin_password gitea_access_token

printf 'Gitea 1.21 is ready at %s; repository %s is available.\n' \
  "$gitea_base_url" "$gitea_repository_name"
