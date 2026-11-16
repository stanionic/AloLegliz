print("=" * 50)
print("FINAL VERIFICATION")
print("=" * 50)

print("\n1. Flask Routes:")
from app import app
routes = [str(r) for r in app.url_map.iter_rules() if str(r) != '/static/<path:filename>']
for r in sorted(routes):
    print("   ", r)
print("   All expected routes present:", all(x in routes for x in ['/', '/manifest.json', '/sw.js', '/evenements.html']))

print("\n2. Events page (frontend/evenements.html):")
with open('frontend/evenements.html', 'r', encoding='utf-8') as f:
    c = f.read()
checks = {
    'PWA meta tags (apple-mobile-web-app-capable)': 'apple-mobile-web-app-capable' in c,
    'PWA manifest link': '/manifest.json' in c,
    'Service worker registration': 'serviceWorker' in c,
    'Theme color': 'theme-color' in c,
    'Apple touch icon': 'apple-touch-icon' in c,
}
for check, result in checks.items():
    print("   [{} {}] {}".format("OK" if result else "MISSING", " " * (40 - len(check)), check))
print("   All PWA checks passed:", all(checks.values()))

print("\n3. Main page (frontend/sos-alo-legliz.html):")
with open('frontend/sos-alo-legliz.html', 'r', encoding='utf-8') as f:
    c = f.read()
# Use ASCII-only checks to avoid encoding issues
checks = {
    'Button "Voir" present': 'Voir' in c,
    'Button "evenements" link': '/evenements.html' in c,
    'Service worker registered': "navigator.serviceWorker.register('/sw.js')" in c,
}
for check, result in checks.items():
    print("   [{} {}] {}".format("OK" if result else "MISSING", " " * (40 - len(check)), check))
print("   All main page checks passed:", all(checks.values()))

print("\n4. Service worker (frontend/sw.js) cache:")
with open('frontend/sw.js', 'r', encoding='utf-8') as f:
    c = f.read()
print("   Events page in cache:", '/evenements.html' in c)

print("\n5. app.py route for events:")
with open('app.py', 'r', encoding='utf-8') as f:
    c = f.read()
print("   evenements route:", '/evenements.html' in c and 'def evenements' in c)

import os
print("\n6. Files in frontend folder:")
for f in sorted(os.listdir('frontend')):
    print("   ", f)
print("\n" + "=" * 50)
print("VERIFICATION COMPLETE")
print("=" * 50)
