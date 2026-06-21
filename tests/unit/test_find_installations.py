# -*- coding: utf-8 -*-
"""Tests for _find_revit_installations with mocked registry and filesystem."""
import sys
import types
from unittest.mock import patch, MagicMock
import pytest
from tools.launch_tools import _find_revit_installations


def _make_mock_winreg(entries=None):
    """Build a mock winreg module.

    entries: list of (subkey_name, value_name, install_dir) tuples, e.g.
        [("Autodesk Revit 2025", "InstallationLocation", "C:/Revit2025/")]

    winreg.OpenKey is called twice per subkey: once with an integer hive
    constant to open the base key, and again with the returned handle to open
    the named subkey.  The original helper only handled the first call, so the
    subkey open always raised OSError and the registry branch was never reachable.
    This version handles both call forms.
    """
    mock_winreg = MagicMock()
    mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
    mock_winreg.HKEY_CURRENT_USER = 0x80000001

    if entries is None:
        entries = []

    def open_key(key_or_hive, name):
        if isinstance(key_or_hive, int):
            # Opening the base key from a hive constant — always succeeds so
            # both HKLM and HKCU can be iterated.
            return MagicMock(name="base_key")
        # Opening a named subkey from a base-key handle.
        for sk_name, val_name, install_dir in entries:
            if sk_name == name:
                h = MagicMock(name=f"subkey_{sk_name}")
                h._val_name = val_name
                h._install_dir = install_dir
                return h
        raise OSError(f"subkey {name!r} not found")

    def enum_key(key, index):
        if index < len(entries):
            return entries[index][0]
        raise OSError("no more subkeys")

    def query_value(key, value_name):
        expected = getattr(key, "_val_name", None)
        if expected is not None and value_name == expected:
            return (key._install_dir, 1)
        raise OSError("value not found")

    mock_winreg.OpenKey = MagicMock(side_effect=open_key)
    mock_winreg.EnumKey = MagicMock(side_effect=enum_key)
    mock_winreg.QueryValueEx = MagicMock(side_effect=query_value)
    mock_winreg.CloseKey = MagicMock()

    return mock_winreg


class TestFindInstallationsFilesystem:
    """Test the filesystem fallback path (no registry)."""

    @patch("tools.launch_tools.os.path.isfile")
    def test_filesystem_finds_revit(self, mock_isfile):
        """Filesystem scan finds Revit 2025."""
        def isfile(path):
            return "Revit 2025" in path and path.endswith("Revit.exe")
        mock_isfile.side_effect = isfile

        # Patch out winreg so registry path fails gracefully
        with patch.dict(sys.modules, {"winreg": None}):
            result = _find_revit_installations()

        assert len(result) == 1
        assert result[0]["year"] == "2025"

    @patch("tools.launch_tools.os.path.isfile")
    def test_multiple_versions_sorted(self, mock_isfile):
        """Multiple filesystem versions are returned newest-first."""
        found_years = {"2024", "2025", "2026"}

        def isfile(path):
            return any(
                f"Revit {y}" in path and path.endswith("Revit.exe")
                for y in found_years
            )
        mock_isfile.side_effect = isfile

        with patch.dict(sys.modules, {"winreg": None}):
            result = _find_revit_installations()

        years = [r["year"] for r in result]
        assert years == ["2026", "2025", "2024"]

    @patch("tools.launch_tools.os.path.isfile", return_value=False)
    def test_no_revit_found(self, mock_isfile):
        """No installations found returns empty list."""
        with patch.dict(sys.modules, {"winreg": None}):
            result = _find_revit_installations()

        assert result == []

    @patch("tools.launch_tools.os.path.isfile", return_value=False)
    def test_deduplication(self, mock_isfile):
        """Registry and filesystem don't produce duplicates for the same year."""
        # Even with no filesystem hits, an empty result is fine
        with patch.dict(sys.modules, {"winreg": None}):
            result = _find_revit_installations()

        # Count unique years
        years = [r["year"] for r in result]
        assert len(years) == len(set(years))


class TestFindInstallationsRegistry:
    """Test the Windows Registry discovery path."""

    def _run_with_registry(self, entries, isdir=True, isfile_exe=True):
        """Helper: run _find_revit_installations with a mocked winreg and fs."""
        mock_winreg = _make_mock_winreg(entries)
        install_dirs = {e[2] for e in entries}
        exe_paths = {e[2].rstrip("/\\") + "/Revit.exe" for e in entries}

        def _isdir(path):
            return path in install_dirs if isdir is not True else True

        def _isfile(path):
            return any(path.replace("\\", "/") == e.replace("\\", "/") for e in exe_paths) if isfile_exe else False

        with patch.dict(sys.modules, {"winreg": mock_winreg}):
            with patch("tools.launch_tools.os.path.isdir", side_effect=_isdir):
                with patch("tools.launch_tools.os.path.isfile", side_effect=_isfile):
                    return _find_revit_installations()

    def test_registry_finds_single_installation(self):
        """Registry returns one Revit installation."""
        entries = [("Autodesk Revit 2025", "InstallationLocation", "C:/Revit2025/")]
        result = self._run_with_registry(entries)

        assert len(result) == 1
        assert result[0]["year"] == "2025"
        assert result[0]["path"] == "C:/Revit2025/Revit.exe"

    def test_registry_finds_multiple_versions_sorted(self):
        """Multiple registry entries are returned newest-first."""
        entries = [
            ("Autodesk Revit 2024", "InstallationLocation", "C:/Revit2024/"),
            ("Autodesk Revit 2026", "InstallationLocation", "C:/Revit2026/"),
            ("Autodesk Revit 2025", "InstallationLocation", "C:/Revit2025/"),
        ]
        result = self._run_with_registry(entries)

        years = [r["year"] for r in result]
        assert years == ["2026", "2025", "2024"]

    def test_registry_deduplicates_with_filesystem(self):
        """A year found in the registry is not duplicated by the filesystem scan."""
        entries = [("Autodesk Revit 2025", "InstallationLocation", "C:/Revit2025/")]
        mock_winreg = _make_mock_winreg(entries)

        # Filesystem also reports Revit 2025
        def _isfile(path):
            return "Revit.exe" in path

        with patch.dict(sys.modules, {"winreg": mock_winreg}):
            with patch("tools.launch_tools.os.path.isdir", return_value=True):
                with patch("tools.launch_tools.os.path.isfile", side_effect=_isfile):
                    result = _find_revit_installations()

        years = [r["year"] for r in result]
        assert years.count("2025") == 1

    def test_registry_subkey_without_exe_is_skipped(self):
        """A registry entry whose install dir has no Revit.exe is not returned."""
        entries = [("Autodesk Revit 2025", "InstallationLocation", "C:/Revit2025/")]
        mock_winreg = _make_mock_winreg(entries)

        with patch.dict(sys.modules, {"winreg": mock_winreg}):
            with patch("tools.launch_tools.os.path.isdir", return_value=True):
                with patch("tools.launch_tools.os.path.isfile", return_value=False):
                    result = _find_revit_installations()

        assert result == []

    def test_registry_hive_missing_falls_through_to_filesystem(self):
        """When winreg raises on OpenKey, the filesystem fallback still works."""
        mock_winreg = MagicMock()
        mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
        mock_winreg.HKEY_CURRENT_USER = 0x80000001
        mock_winreg.OpenKey.side_effect = OSError("key not found")

        def _isfile(path):
            return "Revit 2024" in path and path.endswith("Revit.exe")

        with patch.dict(sys.modules, {"winreg": mock_winreg}):
            with patch("tools.launch_tools.os.path.isfile", side_effect=_isfile):
                result = _find_revit_installations()

        assert len(result) == 1
        assert result[0]["year"] == "2024"
