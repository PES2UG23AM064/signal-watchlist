/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        // Restrained Groww-like green as the single accent; red/green reserved for direction.
        brand: { DEFAULT: "#00b386", dark: "#009973" },
        up: "#00b386",
        down: "#eb5b3c",
      },
    },
  },
  plugins: [],
};
