-- Separate database for the test suite so tests never touch dev data.
CREATE DATABASE flowforge_test OWNER flowforge;
\connect flowforge
CREATE EXTENSION IF NOT EXISTS citext;
\connect flowforge_test
CREATE EXTENSION IF NOT EXISTS citext;
