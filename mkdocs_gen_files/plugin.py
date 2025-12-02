from __future__ import annotations

import logging
import os
import shutil
import runpy
import tempfile
import urllib.parse
from typing import TYPE_CHECKING

from mkdocs.config import Config
from mkdocs.config import config_options as opt
from mkdocs.exceptions import PluginError
from mkdocs.plugins import BasePlugin, event_priority

from .editor import FilesEditor

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.files import Files
    from mkdocs.structure.pages import Page


log = logging.getLogger(f"mkdocs.plugins.{__name__}")


class PluginConfig(Config):
    scripts = opt.ListOfItems(opt.File(exists=True))
    # Optional directory to write generated files into. If not provided, a temp dir is used.
    directory = opt.Optional(opt.Type(str))
    # Whether to delete the auto-created temporary directory after build.
    # Has no effect when a custom directory is provided.
    cleanup = opt.Type(bool, default=True)


class GenFilesPlugin(BasePlugin[PluginConfig]):
    def on_files(self, files: Files, config: MkDocsConfig) -> Files:
        # Determine working directory for generated files
        if self.config.directory:
            self._dir_name = self.config.directory
            os.makedirs(self._dir_name, exist_ok=True)
            # Do not auto-clean custom directory provided by the user
            self._should_cleanup = False
        else:
            # Create a temporary directory (without auto-finalizer cleanup)
            # so that we can honor the cleanup option.
            self._dir_name = tempfile.mkdtemp(prefix="mkdocs_gen_files_")
            self._should_cleanup = bool(self.config.cleanup)

        with FilesEditor(files, config, self._dir_name) as ed:
            for file_name in self.config.scripts:
                try:
                    runpy.run_path(file_name)
                except SystemExit as e:
                    if e.code:
                        raise PluginError(f"Script {file_name!r} caused {e!r}")

        self._edit_paths = dict(ed.edit_paths)
        # Best-effort workaround for an interaction with `edit_uri_template`:
        for src_path, path in self._edit_paths.items():
            try:
                if path is not None:
                    f = files.get_file_from_path(src_path)
                    if f is not None:
                        f.edit_uri = path
            except Exception:
                pass

        return ed.files

    def on_page_content(self, html, page: Page, config: MkDocsConfig, files: Files):
        repo_url = config.repo_url
        edit_uri = config.edit_uri

        src_path = page.file.src_uri
        if src_path in self._edit_paths:
            path = self._edit_paths.pop(src_path)
            if repo_url and edit_uri:
                # Ensure urljoin behavior is correct
                if not edit_uri.startswith(("?", "#")) and not repo_url.endswith("/"):
                    repo_url += "/"

                page.edit_url = path and urllib.parse.urljoin(
                    urllib.parse.urljoin(repo_url, edit_uri), path
                )

        return html

    @event_priority(-100)
    def on_post_build(self, config: MkDocsConfig):
        if self._should_cleanup:
            try:
                shutil.rmtree(self._dir_name, ignore_errors=True)
            except Exception:
                pass

        unused_edit_paths = {k: str(v) for k, v in self._edit_paths.items() if v}
        if unused_edit_paths:
            msg = "mkdocs_gen_files: These set_edit_path invocations went unused (the files don't exist): %r"
            log.warning(msg, unused_edit_paths)
