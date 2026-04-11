# shubhamojhaandassociates.com

Single-page static site for Shubham Ojha & Associates, Advocates, Jodhpur.

- **No build step.** Pure HTML, CSS, JS. Open `index.html` locally.
- **Deploy targets:** Cloudflare Pages, GitHub Pages, Netlify — drag `index.html` and `assets/` onto any of them.
- **SEO:** Open Graph tags + `LegalService` JSON-LD structured data (ready for Google Business Profile integration — claim the profile at `https://www.google.com/business/`, then paste `https://shubhamojhaandassociates.com` as the website).
- **BCI compliance:** First-visit disclaimer modal per Bar Council of India Rules (advertising prohibition). Users must click "I Agree" before seeing the site.
- **Contact form:** Posts to Formspree if `FORMSPREE_ENDPOINT` is set in the inline script; otherwise falls back to `mailto:`. To enable Formspree:
  1. Sign up at formspree.io, create a new form, copy the endpoint.
  2. Edit `index.html` and set `const FORMSPREE_ENDPOINT = "https://formspree.io/f/xxxxxxx";`.

## Stable promotion path

This version lives in `website/` (top of the repo). To deploy:

```bash
# Cloudflare Pages
wrangler pages deploy website/ --project-name shubham-ojha-associates

# GitHub Pages
git add website/
# enable Pages on the main branch, path = /website

# Netlify
netlify deploy --dir=website --prod
```

## Google Business Profile tie-in

Once the site is up:

1. Go to business.google.com, claim "Shubham Ojha & Associates".
2. Category: Legal Services → Lawyer / Advocate.
3. Website: https://shubhamojhaandassociates.com
4. Hours: match the `openingHoursSpecification` in the JSON-LD block.
5. Add photos (office exterior, interior, advocate photo).
6. Verify by postcard (takes 5–14 days).

The JSON-LD structured data already describes the firm to Google; claiming the Business Profile accelerates recognition.
