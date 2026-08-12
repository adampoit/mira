import * as tailwindPlugin from "prettier-plugin-tailwindcss"

export default {
  endOfLine: "lf",
  semi: false,
  singleQuote: false,
  tabWidth: 2,
  trailingComma: "es5",
  printWidth: 80,
  plugins: [tailwindPlugin],
  tailwindStylesheet: "src/index.css",
  tailwindFunctions: ["cn", "cva"],
}
