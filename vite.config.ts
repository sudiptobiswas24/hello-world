import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // relative asset paths so the build works from any sub-path (e.g. GitHub Pages)
  base: './',
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
