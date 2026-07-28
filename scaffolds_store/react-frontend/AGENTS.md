# React Frontend Conventions

## Project Structure
- `src/app/` — Next.js App Router pages and layouts
- `src/components/` — shared UI components
- `src/lib/` — utilities, API clients, helpers
- `public/` — static assets
- `tests/` — test files mirroring `src/` layout

## Component Patterns
- Arrow function components with named exports
- Explicit `interface` or `type` for props
- One component per file, file named after component
- Co-locate CSS modules or Tailwind classes

## Styling
- Tailwind CSS as primary styling approach
- `cn()` utility for conditional class merging
- CSS modules for complex component-specific styles
- Avoid inline styles except for dynamic values

## State Management
- TanStack Query (React Query) for server state
- React context for global UI state (theme, auth)
- URL search params for filter/sort state
- `useReducer` for complex local state, not `useState` chains

## Testing
- Vitest + React Testing Library
- Tests co-located with components as `*.test.tsx`
- Prefer `@testing-library/user-event` over `fireEvent`
- Mock API calls via MSW (Mock Service Worker)

## TypeScript
- `strict: true` in tsconfig
- Prefer `interface` over `type` for object shapes
- Avoid `any` — use `unknown` and narrow with type guards
- Use `satisfies` operator for type validation

## Package Management
- npm for package management
- Keep dependencies in `dependencies` (runtime) vs `devDependencies` (tooling)
- Regular `npm audit` and dependency updates
