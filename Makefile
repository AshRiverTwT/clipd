PREFIX ?= /usr
DESTDIR ?=
BINDIR := $(DESTDIR)$(PREFIX)/bin
LIBDIR := $(DESTDIR)$(PREFIX)/lib/clipd
UNITDIR := $(DESTDIR)$(PREFIX)/lib/systemd/user
SHAREDIR := $(DESTDIR)$(PREFIX)/share/clipd
PYTHON ?= python3

.PHONY: all check test install uninstall clean

all: check

check:
	$(PYTHON) -m compileall -q src
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -q

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

install:
	install -d "$(BINDIR)" "$(LIBDIR)" "$(UNITDIR)" "$(SHAREDIR)/hypr"
	install -Dm755 bin/clipd "$(BINDIR)/clipd"
	cp -a src/clipd "$(LIBDIR)/"
	find "$(LIBDIR)" -type d -name __pycache__ -prune -exec rm -rf {} +
	install -Dm644 data/clipd.service "$(UNITDIR)/clipd.service"
	install -Dm644 data/config.toml "$(SHAREDIR)/config.toml"
	install -Dm644 data/clipd.rasi "$(SHAREDIR)/clipd.rasi"
	install -Dm644 data/clipd.rasi "$(LIBDIR)/clipd/data/clipd.rasi"
	install -Dm644 hypr/clipd.lua "$(SHAREDIR)/hypr/clipd.lua"
	install -Dm644 hypr/clipd.conf "$(SHAREDIR)/hypr/clipd.conf"
	install -Dm644 LICENSE "$(SHAREDIR)/LICENSE"
	install -Dm644 README.md "$(SHAREDIR)/README.md"
	@echo
	@echo "Installed. Next:"
	@echo "  systemctl --user daemon-reload"
	@echo "  systemctl --user start clipd"
	@echo "  clipd setup --dry-run"
	@echo "  clipd doctor"

uninstall:
	rm -f "$(BINDIR)/clipd"
	rm -f "$(UNITDIR)/clipd.service"
	rm -rf "$(LIBDIR)"
	rm -rf "$(SHAREDIR)"

clean:
	rm -rf src/clipd/__pycache__ src/clipd/*/__pycache__ tests/__pycache__
