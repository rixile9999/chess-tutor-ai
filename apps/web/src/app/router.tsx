import { createBrowserRouter, Navigate } from 'react-router-dom';
import { Shell } from './Shell';
import GamesPage from '../pages/games';
import ReviewPage from '../pages/review';
import ProfilePage from '../pages/profile';
import OpeningsPage from '../pages/openings';
import TrainingPage from '../pages/training';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <Navigate to="/games" replace /> },
      { path: 'games', element: <GamesPage /> },
      { path: 'review', element: <ReviewPage /> },
      { path: 'review/:gameId', element: <ReviewPage /> },
      { path: 'review/:gameId/:ply', element: <ReviewPage /> },
      { path: 'profile', element: <ProfilePage /> },
      { path: 'profile/:username', element: <ProfilePage /> },
      { path: 'openings', element: <OpeningsPage /> },
      { path: 'training', element: <TrainingPage /> },
    ],
  },
]);
