# SnapFind AI on Render Free

1. Create a GitHub repository and upload the extracted `snapfind` folder and `render.yaml` into its root. Keep the repository private if you wish. Do not upload your local data folder, virtual environment, passwords or model files.
2. Sign in at https://dashboard.render.com/ and select **New → Blueprint**. Connect that repository. The blueprint defines one Docker web service on the Free plan, with no paid resources. Check the plan displayed before deploying.
3. When prompted for ADMIN_PASSWORD, enter a new password you will use in the Organizer Dashboard. Render generates the signing key for you.
4. Once the service says Live, open its HTTPS onrender.com URL. Open **Organizer Dashboard**, create a test event, and upload a few consenting test photos. Copy the guest URL for that event and test it on a separate phone. Use a nearby face and follow the blink and head-turn prompts.

Render Free stores uploads and events on an ephemeral filesystem. They can disappear after a restart, redeploy or idle spin-down, so use this package to test a few photos in one session. Your local Mac data is separate. The live link belongs to the deployed service and is assigned by Render.
