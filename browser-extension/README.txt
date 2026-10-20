Password Manager (jblol.systems): browser extension
====================================================
It fills your logins on websites while your USB stick is plugged in. It only talks to the Password Manager app on THIS computer.
Nothing goes over the internet, and the pairing code only works from a browser extension, never from a web page.

1) Make sure the Password Manager app is running (plugging in the stick opens it) and the vault is unlocked.
2) In the app: Settings > "Fill logins in my browser" > Copy the pairing code.

BRAVE (or Chrome)
  a. Open  brave://extensions  and turn on "Developer mode" (top right).
  b. Click "Load unpacked" and choose the folder  PasswordManager-Extension  in your home folder.
  c. Click the puzzle-piece icon, pin "Password Manager", click it, open "Pairing", paste the code, click Save.
  (Brave keeps unpacked extensions between restarts.)

FIREFOX
  Firefox only keeps extensions that Mozilla has signed, so there are two ways:
  Quick try (lasts until Firefox restarts):
     Open  about:debugging#/runtime/this-firefox  > "Load Temporary Add-on..." > pick  manifest.json  in the folder.
  Permanent (free, takes a few minutes, done once):
     1. Make a free account at  addons.mozilla.org  and open  https://addons.mozilla.org/developers/addon/submit/distribution
     2. Choose "On your own" (unlisted), upload  PasswordManager-Extension.zip  from your home folder. Mozilla checks and signs it.
     3. Download the signed .xpi. In Firefox open  about:addons > the gear icon > "Install Add-on From File..." and pick the .xpi.
     4. Click the Password Manager icon in the toolbar, open "Pairing", paste the code, Save.

USING IT
  - One saved login for a site: it fills automatically when the page loads. Two or more (different accounts): a small picker appears under the password box.
  - The toolbar icon shows how many logins match the page. "Fill this page" in its popup fills on demand.
  - It never fills a form that sends your password to a different website, and it only works on https pages.
  - Stick out = vault locked = nothing fills.
