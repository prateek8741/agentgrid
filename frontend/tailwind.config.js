/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // White-label: the tenant's primary colour is injected as a CSS variable at runtime.
        brand: { DEFAULT: "rgb(var(--brand) / <alpha-value>)", soft: "rgb(var(--brand) / 0.1)" },
      },
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"] },
    },
  },
  plugins: [],
};
