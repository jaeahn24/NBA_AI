import os
from datetime import datetime

import numpy as np
import pandas as pd
import wandb
from dotenv import load_dotenv
from evaluation import create_evaluations
from joblib import dump
from modeling_utils import load_featurized_modeling_data
from sklearn.linear_model import Ridge
from sklearn.metrics import log_loss
from sklearn.model_selection import RandomizedSearchCV
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

load_dotenv()
PROJECT_ROOT = os.getenv("PROJECT_ROOT")

if __name__ == "__main__":
    db_path = f"{PROJECT_ROOT}/data/NBA_AI.sqlite"

    log_to_wandb = True  # Set to False to disable logging to Weights & Biases

    model_type = "Ridge_Regression"
    run_datetime = datetime.now().isoformat()

    # -------------------------
    # Section 1: Data Loading
    # -------------------------

    # Define the seasons for training and testing
    training_seasons = ["2020-2021", "2021-2022", "2022-2023"]
    testing_seasons = ["2023-2024"]

    # Load featurized modeling data for the defined seasons
    print("Loading featurized modeling data...")
    print(f"Training seasons: {training_seasons}")
    print(f"Testing seasons: {testing_seasons}")
    training_df = load_featurized_modeling_data(training_seasons, db_path)
    testing_df = load_featurized_modeling_data(testing_seasons, db_path)
    print(f"Training data shape: {training_df.shape}")
    print(f"Testing data shape: {testing_df.shape}")

    # Drop rows with NaN values to ensure data quality
    print("\nDropping rows with NaN values...")
    training_df = training_df.dropna()
    testing_df = testing_df.dropna()
    print(f"Training data shape after dropping NaNs: {training_df.shape}")
    print(f"Testing data shape after dropping NaNs: {testing_df.shape}")

    # -----------------------------------------
    # Section 2: Feature and Target Selection
    # -----------------------------------------

    # Define features (X) by dropping target and non-predictive columns
    # Define targets (y) for the model
    game_info_columns = [
        "game_id",
        "date_time_est",
        "home_team",
        "away_team",
        "season",
        "season_type",
    ]
    game_results_columns = [
        "home_score",
        "away_score",
        "total",
        "home_margin",
        "players_data",
    ]

    X_train = training_df.drop(columns=game_info_columns + game_results_columns)
    y_train = training_df[["home_score", "away_score"]]
    X_test = testing_df.drop(columns=game_info_columns + game_results_columns)
    y_test = testing_df[["home_score", "away_score"]]

    # Keep a list of feature names for later use (e.g., for model interpretation)
    feature_names = X_train.columns.tolist()

    print("\nX_train shape:", X_train.shape)
    print("y_train shape:", y_train.shape, y_train.columns.tolist())
    print("X_test shape:", X_test.shape)
    print("y_test shape:", y_test.shape, y_test.columns.tolist())

    # -------------------------------
    # Section 3: Data Preprocessing
    # -------------------------------

    # Initialize and fit a scaler to standardize features
    print("\nStandardizing features...")
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # -----------------------------------------------------
    # Section 4: Hyperparameter Tuning and Model Training
    # -----------------------------------------------------

    print("\nTraining the model...")
    print("Performing hyperparameter tuning...")
    # Define the hyperparameter space and setup RandomizedSearchCV
    param_distributions = {
        "alpha": np.logspace(-4, 4, 20),  # Defines a range of values for alpha
        "fit_intercept": [
            True,
            False,
        ],  # Whether to calculate the intercept for this model
    }

    # Perform hyperparameter tuning using Randomized Search
    random_search = RandomizedSearchCV(
        Ridge(), param_distributions, n_iter=10, cv=5, random_state=42
    )
    random_search.fit(X_train_scaled, y_train)
    best_params = random_search.best_params_

    # Get the trained model with the best hyperparameters refit on the full training data
    model = random_search.best_estimator_

    # -------------------------------
    # Section 5: Making Predictions
    # -------------------------------

    # Predict on training and testing data
    print("\nMaking predictions...")
    y_pred = model.predict(X_test_scaled)
    y_pred_train = model.predict(X_train_scaled)

    # Extract specific prediction components for analysis
    y_pred_home_score = y_pred[:, 0]
    y_pred_away_score = y_pred[:, 1]
    y_pred_home_margin = y_pred_home_score - y_pred_away_score

    y_pred_train_home_score = y_pred_train[:, 0]
    y_pred_train_away_score = y_pred_train[:, 1]
    y_pred_train_home_margin = y_pred_train_home_score - y_pred_train_away_score

    # Calculate win probabilities using the logistic (sigmoid) function
    def win_prob(score_diff):
        a = -0.2504  # Intercept term
        b = 0.1949  # Slope term
        win_prob = 1 / (1 + np.exp(-(a + b * score_diff)))
        return win_prob

    y_pred_home_win_prob = win_prob(y_pred_home_margin)
    y_pred_train_home_win_prob = win_prob(y_pred_train_home_margin)

    # -----------------------------
    # Section 6: Model Evaluation
    # -----------------------------

    print("\nEvaluating the model...")
    # Core Metrics
    home_score_mae = np.mean(np.abs(y_test["home_score"] - y_pred_home_score))
    away_score_mae = np.mean(np.abs(y_test["away_score"] - y_pred_away_score))
    home_margin_mae = np.mean(
        np.abs(y_test["home_score"] - y_test["away_score"] - y_pred_home_margin)
    )
    home_win_prob_log_loss = log_loss(
        (y_test["home_score"] > y_test["away_score"]).astype(int), y_pred_home_win_prob
    )

    print("\nCore Metrics:")
    print(f"Home Score MAE: {home_score_mae:.2f}")
    print(f"Away Score MAE: {away_score_mae:.2f}")
    print(f"Home Margin MAE: {home_margin_mae:.2f}")
    print(f"Home Win Probability Log Loss: {home_win_prob_log_loss:.4f}")

    # Run full evaluation suite
    # Prepare correct and predicted values for evaluation
    train_correct = {
        "home_score": y_train["home_score"],
        "away_score": y_train["away_score"],
        "home_margin_derived": y_train["home_score"] - y_train["away_score"],
        "total_points_derived": y_train["home_score"] + y_train["away_score"],
        "home_win_prob": (y_train["home_score"] > y_train["away_score"]).astype(int),
    }
    train_pred = {
        "home_score": y_pred_train_home_score,
        "away_score": y_pred_train_away_score,
        "home_margin_derived": y_pred_train_home_margin,
        "total_points_derived": y_pred_train_home_score + y_pred_train_away_score,
        "home_win_prob": y_pred_train_home_win_prob,
    }

    test_correct = {
        "home_score": y_test["home_score"],
        "away_score": y_test["away_score"],
        "home_margin_derived": y_test["home_score"] - y_test["away_score"],
        "total_points_derived": y_test["home_score"] + y_test["away_score"],
        "home_win_prob": (y_test["home_score"] > y_test["away_score"]).astype(int),
    }

    test_pred = {
        "home_score": y_pred_home_score,
        "away_score": y_pred_away_score,
        "home_margin_derived": y_pred_home_margin,
        "total_points_derived": y_pred_home_score + y_pred_away_score,
        "home_win_prob": y_pred_home_win_prob,
    }

    train_evaluations = pd.DataFrame(
        [
            {
                "train_" + k: v
                for k, v in create_evaluations(train_correct, train_pred).items()
            }
        ]
    )

    test_evaluations = pd.DataFrame(
        [
            {
                "test_" + k: v
                for k, v in create_evaluations(test_correct, test_pred).items()
            }
        ]
    )

    # Create a DataFrame with the intercept and coefficients
    model_details = pd.DataFrame(
        [
            {
                "feature_" + feature: value
                for feature, value in zip(
                    ["intercept"] + feature_names,
                    [intercept] + list(coefs),
                )
            }
            for intercept, coefs in zip(model.intercept_, model.coef_)
        ]
    )

    # ------------------------------------------
    # Section 7: Recreating Model on Full Data
    # ------------------------------------------

    print("\nRecreating the model on full data...")
    X_all = np.concatenate((X_train, X_test))
    y_all = np.concatenate((y_train, y_test))

    final_scaler = StandardScaler()
    X_all_scaled = final_scaler.fit_transform(X_all)

    final_random_search = RandomizedSearchCV(
        Ridge(), param_distributions, n_iter=10, cv=5, random_state=42
    )
    final_random_search.fit(X_all_scaled, y_all)

    # The best model, refit on the whole training set
    final_model = final_random_search.best_estimator_

    pipeline = Pipeline([("scaler", final_scaler), ("model", final_model)])

    # -----------------------------
    # Section 8: Saving the Model
    # -----------------------------

    # Construct filename and save the pipeline
    print("\nSaving the model...")
    model_filename = f"{PROJECT_ROOT}/models/{model_type}_{run_datetime}.joblib"
    dump(pipeline, model_filename)
    print(f"Model saved to {model_filename}\n")

    # ----------------------------------------
    # Section 9: Logging to Weights & Biases
    # ----------------------------------------

    if not log_to_wandb:
        print("\nLogging to Weights & Biases disabled.")
        exit()

    # Initialize wandb for experiment tracking
    run = wandb.init(project="NBA AI", config=best_params)

    # Log configuration and model details
    wandb.config.update(
        {
            "model_type": model_type,
            "train_seasons": training_seasons,
            "train_season_count": len(training_seasons),
            "test_seasons": testing_seasons,
            "train_shape": X_train_scaled.shape,
            "test_shape": X_test_scaled.shape,
            "targets": ["home_score", "away_score"],
            "features": feature_names,
            "run_datetime": run_datetime,
        }
    )

    # Log core metrics
    wandb.summary.update(
        {
            "home_score_mae": home_score_mae,
            "away_score_mae": away_score_mae,
            "home_margin_mae": home_margin_mae,
            "home_win_prob_log_loss": home_win_prob_log_loss,
        }
    )

    # Log the full evaluation suite
    train_evaluations_table = wandb.Table(dataframe=train_evaluations)
    test_evaluations_table = wandb.Table(dataframe=test_evaluations)
    wandb.summary.update({"Train Evals": train_evaluations_table})
    wandb.summary.update({"Test Evals": test_evaluations_table})

    # Log the model details (intercept and coefficients)
    model_details_table = wandb.Table(dataframe=model_details)
    wandb.summary.update({"Model Details": model_details_table})

    # Save the model to wandb
    # Make sure model_filename is a file path to your saved model
    wandb.save(model_filename, base_path=PROJECT_ROOT)

    # End the wandb run
    run.finish()