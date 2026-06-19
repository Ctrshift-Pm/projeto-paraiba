import { defineConfig } from 'vite';

export default defineConfig({
  root: '.',
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: {
        main: 'index.html',
        cadastros: 'cadastros.html',
        rag: 'rag.html',
        gemini: 'gemini.html',
      },
    },
  },
});
