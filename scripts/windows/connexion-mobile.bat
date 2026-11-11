@echo off
rem Lance connexion-mobile.ps1 avec les droits administrateur (necessaires pour netsh et le pare-feu).
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%~dp0connexion-mobile.ps1\"'"
