#!/bin/sh
# Устанавливает pre-commit hook, защищающий правило ядра.
# Запускать из корня репозитория: sh tools/install_hooks.sh
set -eu

root=$(git rev-parse --show-toplevel)
hooks="$root/.git/hooks"
mkdir -p "$hooks"

cat > "$hooks/pre-commit" <<'HOOK'
#!/bin/sh
# Правило: изменение ядра (tools/*.py, tools/*.html) коммитится вместе со сменой tools/VERSION.
if ! command -v python3 >/dev/null 2>&1; then
    echo "pre-commit: python3 не найден, проверка ядра пропущена" >&2
    exit 0
fi
exec python3 "$(git rev-parse --show-toplevel)/tools/guard_core.py" --staged
HOOK

chmod +x "$hooks/pre-commit"
echo "установлен $hooks/pre-commit"
echo "отключить для одного коммита: git commit --no-verify"
