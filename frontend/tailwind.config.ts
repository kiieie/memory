import type { Config } from "tailwindcss";

// 접근성 기준(docs/reference/frontend-spec.md#기술-세부): 기본 폰트 18px, 터치 타겟 48px 이상.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
};

export default config;
