/** Tailwind config for the compiled production build (see build/README notes
 * in WEBSITE_AUDIT_REPORT.md for the rebuild command). This mirrors the
 * inline `tailwind.config` that used to live in base.html / error_404.html /
 * error_500.html / not_found.html when they loaded the cdn.tailwindcss.com
 * "play CDN" script — same brand color remapping, same font families, same
 * typography plugin, now compiled ahead of time instead of in the browser.
 */
module.exports = {
  content: [
    "./app/templates/**/*.html",
  ],
  // blog.html / blog_detail.html build classes like `bg-{{ post.color }}-100`
  // and `text-{{ post.color }}-600` from blog_content.py's per-post `color`
  // field. Tailwind's scanner only sees the literal `{{ post.color }}`
  // template syntax, not the color names Python fills in, so those classes
  // must be safelisted explicitly or they silently go missing from the
  // compiled CSS. Currently used: blue, green, orange, purple — the rest
  // are included as headroom for the next blog post's color without
  // needing a config change; if a genuinely new color is ever used, add it
  // here too.
  safelist: [
    "blue", "green", "orange", "purple", "red", "amber", "teal", "slate",
  ].flatMap((c) => [`bg-${c}-100`, `text-${c}-600`]),
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        heading: ['Poppins', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
      // Existing blue/indigo/gray utility classes across the templates
      // resolve to Avigronix brand colors instead of Tailwind's stock
      // palette (kept as remapped stock names, not a custom "brand"
      // namespace, matching the original inline config).
      colors: {
        blue: {
          50: '#F5F9FF', 100: '#E5F0FF', 200: '#CCE1FF', 300: '#ACCEFF',
          400: '#7FB3FF', 500: '#538EF5', 600: '#2F6FED', 700: '#285EC8',
          800: '#1F4999', 900: '#153367', 950: '#0B1B33',
        },
        indigo: {
          50: '#ECF4FF', 100: '#CCE1FF', 200: '#73A9FC', 300: '#5F98F8',
          400: '#4783F2', 500: '#2D6BE4', 600: '#2960CC', 700: '#214FA6',
          800: '#193D7D', 900: '#112A54', 950: '#0B1B33',
        },
        gray: {
          50: '#F8FAFC', 100: '#ECEFF3', 200: '#E0E3E8', 300: '#C1C7D1',
          400: '#9AA5B4', 500: '#64748B', 600: '#57677E', 700: '#405068',
          800: '#2A3A52', 900: '#0B1B33', 950: '#071221',
        },
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
};
