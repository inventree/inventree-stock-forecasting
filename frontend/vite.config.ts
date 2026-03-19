import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteExternalsPlugin } from 'vite-plugin-externals'


const baseRollupOptions = {
  dir: '../inventree_forecasting/static',
  globals: {
    react: 'React',
    'react-dom': 'ReactDOM',
    '@mantine/core': 'MantineCore',
    "@mantine/notifications": 'MantineNotifications',
  }
};

/**
 * Vite config to build the frontend plugin as an exported module.
 * This will be distributed in the 'static' directory of the plugin.
 */
export default defineConfig({
  plugins: [
    react({
      jsxRuntime: 'classic'
    }),
    viteExternalsPlugin({
      react: 'React',
      'react-dom': 'ReactDOM',
      '@mantine/core': 'MantineCore',
      "@mantine/notifications": 'MantineNotifications',
    }),
  ],
  esbuild: {
    jsx: 'preserve',
  },
  build: {
    // minify: false,
    cssCodeSplit: false,
    manifest: true,
    sourcemap: true,
    rollupOptions: {
      preserveEntrySignatures: "exports-only",
      input: [
        './src/ForecastingPanel.tsx',
      ],
      output: [
        {
          ...baseRollupOptions,
          entryFileNames: '[name].js',
          assetFileNames: 'assets/[name].[ext]',
        },
        {
          ...baseRollupOptions,
          entryFileNames: '[name]-[hash].min.js',
          assetFileNames: 'assets/[name].[ext]',
        }
      ],
      external: ['react', 'react-dom', '@mantine/core', '@mantine/notifications'],
    }
  },
  optimizeDeps: {
    exclude: ['react', 'react-dom', '@mantine/core', '@mantine/notifications'],
  },
})
