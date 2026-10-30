# agentes-entrenamiento

Entrenamiento del equipo diario de la oficina de agentes de inversión: viven otra vez los días de bolsa del
3 de agosto al 29 de octubre de 2026 con los mismos programas (carpeta `agentes/`, copiada de la oficina).

- Todo con Gemini Flash Lite (el principal solo deja 20 peticiones al día).
- Cada día el reloj se congela a las 11:00 de Nueva York y el mercado solo enseña lo que se sabía a esa hora.
- No se manda ninguna orden a Alpaca y no se despide a nadie.
- Cada ejecución sigue por donde iba la anterior (`datos/progreso.json`) hasta que se acaba la cuota diaria de la IA.
- `RESULTADOS.md` resume cómo le habría ido a cada estrategia y las reglas que ha aprendido cada agente.

Lo lanza cron-job.org cada 6 horas, o a mano desde Actions → Entrenamiento → Run workflow.
