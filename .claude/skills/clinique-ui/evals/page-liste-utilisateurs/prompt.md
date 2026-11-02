---
tags: [clinique-ui]
max_turns: 25
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Dans mon application Clinique App (django_backend/frontend/), ajoute une page journal_connexions.html qui liste les connexions récentes : qui s'est connecté, quand, depuis quelle adresse, et si ça a réussi ou échoué. Il faut pouvoir filtrer par utilisateur. L'API /api/audit-logs existe déjà et renvoie {total, entries:[{user_email, action, ip_address, created_at, details}]} — les actions sont "login" et "login_failed". Fais juste la page, pas le serveur.
